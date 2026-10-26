"""Break time: what is allowed, what was taken, and what is paid.

  - a shift under 6 hours has no break
  - 6 hours up to and including 12 hours: 30 minutes
  - more than 12 hours: 60 minutes
  - break time up to the allowance is paid; time beyond it is not (we report the minutes, payroll is separate)

Two lengths are checked. The rota's shift length says what is allowed while the break is taken.
The hours actually worked confirm it at clock-out. If they give different paid time, a manager
decides which one counts (record.break_basis); until then the hours actually worked are used.
"""
from apps.rota.services import shift_span

from .models import ClockDevice

SHORT_SHIFT_MINUTES = 6 * 60
LONG_SHIFT_MINUTES = 12 * 60
STANDARD_BREAK = 30
LONG_BREAK = 60


def allowance_minutes(shift_minutes):
    """Break minutes allowed for a shift of this length."""
    if shift_minutes < SHORT_SHIFT_MINUTES:
        return 0
    if shift_minutes > LONG_SHIFT_MINUTES:
        return LONG_BREAK
    return STANDARD_BREAK


def minutes_between(start, end):
    return int((end - start).total_seconds() // 60)


def rota_minutes(record):
    """Length of the rostered shift, or None for a record with no shift."""
    shift = record.shift
    if shift is None:
        return None
    begins, finishes = shift_span(shift.date, shift.start_time, shift.end_time)
    return minutes_between(begins, finishes)


def taken_minutes(record, now):
    total = 0
    for period in record.breaks.all():
        end = period.ended_at or record.clock_out_at or now
        total += max(0, minutes_between(period.started_at, end))
    return total


def summary(record, now):
    """Allowed, taken, paid and unpaid break minutes for one record."""
    rota = rota_minutes(record)
    actual = record.worked_minutes
    rota_allowed = allowance_minutes(rota) if rota is not None else None
    actual_allowed = allowance_minutes(actual) if actual is not None else None
    if record.break_basis == "rota" and rota_allowed is not None:
        allowed, basis = rota_allowed, "rota"
    elif actual_allowed is not None:
        allowed, basis = actual_allowed, "actual"
    elif rota_allowed is not None:
        allowed, basis = rota_allowed, "rota"
    else:
        allowed, basis = 0, "none"
    taken = taken_minutes(record, now)
    paid = min(taken, allowed)
    return {
        "rota_minutes": rota, "worked_minutes": actual,
        "rota_allowed": rota_allowed, "actual_allowed": actual_allowed,
        "basis": basis, "allowed": allowed, "taken": taken, "paid": paid,
        "unpaid": taken - paid, "unused": allowed - paid,
    }


def tracked(record):
    """Breaks only matter where they are recorded: a shop with a fingerprint device, or a record with a break."""
    return record.breaks.exists() or ClockDevice.objects.filter(store=record.store, is_active=True).exists()


def finish(record, now):
    """Called when the person clocks out at `now`: end any break left open and return the flags to add."""
    if not tracked(record):
        return []
    flags = []
    open_break = record.breaks.filter(ended_at__isnull=True).first()
    if open_break is not None:
        open_break.ended_at = now if now > open_break.started_at else None
        if open_break.ended_at is None:
            open_break.delete()
        else:
            open_break.save(update_fields=["ended_at"])
            flags.append("break_not_ended")

    taken = taken_minutes(record, now)
    actual_allowed = allowance_minutes(minutes_between(record.clock_in_at, now))
    rota = rota_minutes(record)
    if taken > 0:
        if actual_allowed == 0:
            flags.append("break_not_allowed")
        elif taken > actual_allowed:
            flags.append("break_over")
        if rota is not None and min(taken, allowance_minutes(rota)) != min(taken, actual_allowed):
            flags.append("break_hours_differ")
    elif actual_allowed > 0:
        flags.append("no_break")
    return flags
