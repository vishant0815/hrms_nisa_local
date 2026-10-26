"""Hours to pay. Everything here reads attendance and writes nothing.

A record is paid when it is finished and nobody has a question about it:
  - rejected by a manager: never paid, not listed
  - still clocked in, or waiting for a manager's decision: held
  - clocked time longer than the rota by more than the tolerance, with no manager OK: held
  - no hourly rate for that day: held
Held records are not lost: they are offered again in every later run until they are paid.

Paid minutes = time between clock-in and clock-out, minus break time beyond the allowance.
Pay for each rate = minutes x rate / 60, rounded to the penny (half up), then added up.
"""
from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.utils import timezone

from apps.attendance.models import AttendanceRecord, ReviewStatus
from apps.fingerprint import breaks

from .models import HoursApproval, PayRate

PENNY = Decimal("0.01")


def pounds(value) -> Decimal:
    return Decimal(value).quantize(PENNY, rounding=ROUND_HALF_UP)


def tax_year_start(on):
    start = on.replace(month=4, day=6)
    return start.replace(year=start.year - 1) if on < start else start


def tax_year_label(on):
    start = tax_year_start(on)
    return f"{start.year}/{(start.year + 1) % 100:02d}"


def tax_week(on):
    """UK tax week: week 1 starts on 6 April."""
    return (on - tax_year_start(on)).days // 7 + 1


def start_of_day(day):
    return timezone.make_aware(datetime.combine(day, time.min))


def local_day(moment):
    return timezone.localtime(moment).date()


def hhmm(moment):
    return timezone.localtime(moment).strftime("%H:%M")


def rate_on(rates, day):
    """`rates` is newest first. The rate in force on `day`, or None."""
    for rate in rates:
        if rate.effective_from <= day:
            return rate.hourly_rate
    return None


def classify(record, now, approved_ids, tolerance):
    """None when the record can be paid, else the reason it is held."""
    if record.clock_out_at is None:
        return "still_clocked_in"
    if record.review_status == ReviewStatus.PENDING:
        return "needs_review"
    rota = breaks.rota_minutes(record)
    if (
        rota is not None and record.worked_minutes > rota + tolerance
        and record.review_status != ReviewStatus.APPROVED and record.pk not in approved_ids
    ):
        return "extra_hours"
    return None


def record_row(record, now):
    unpaid = breaks.summary(record, now)["unpaid"] if breaks.tracked(record) else 0
    taken = breaks.taken_minutes(record, now)
    unpaid = min(unpaid, record.worked_minutes)
    shift = record.shift
    return {
        "record_id": record.pk,
        "date": local_day(record.clock_in_at).isoformat(),
        "rota": f"{shift.start_time:%H:%M}-{shift.end_time:%H:%M}" if shift else "",
        "clocked": f"{hhmm(record.clock_in_at)}-{hhmm(record.clock_out_at)}",
        "break_taken": taken,
        "unpaid_break": unpaid,
        "paid_minutes": record.worked_minutes - unpaid,
        "source": record.source,
    }


def first_run_start():
    """Attendance before the first pay run is history that was paid some other way."""
    from .models import PayRun

    first = PayRun.objects.order_by("period_start").first()
    return first.period_start if first else None


def build_payslip_data(employee, period_end, paid_ids, now, since):
    """The numbers for one person, or None when there is nothing to show."""
    records = (
        AttendanceRecord.objects.filter(
            employee=employee, clock_in_at__lt=start_of_day(period_end + timedelta(days=1)),
            clock_in_at__gte=start_of_day(since),
        )
        .exclude(pk__in=paid_ids).exclude(review_status=ReviewStatus.REJECTED)
        .select_related("shift", "store").prefetch_related("breaks").order_by("clock_in_at")
    )
    records = list(records)
    if not records:
        return None
    approved_ids = set(HoursApproval.objects.filter(record__in=records).values_list("record_id", flat=True))
    rates = list(PayRate.objects.filter(employee=employee).order_by("-effective_from"))
    tolerance = settings.PAYROLL_EXTRA_HOURS_TOLERANCE_MINUTES

    days, held, minutes_by_rate = [], [], defaultdict(int)
    store_minutes = defaultdict(int)
    for record in records:
        reason = classify(record, now, approved_ids, tolerance)
        rate = rate_on(rates, local_day(record.clock_in_at))
        if reason is None and rate is None:
            reason = "no_rate"
        if reason:
            held.append({
                "record_id": record.pk, "date": local_day(record.clock_in_at).isoformat(), "reason": reason,
                "minutes": record.worked_minutes, "store_id": record.store_id,
            })
            continue
        row = record_row(record, now)
        row["rate"] = str(rate)
        days.append(row)
        minutes_by_rate[rate] += row["paid_minutes"]
        store_minutes[record.store_id] += row["paid_minutes"]

    earnings, gross = [], Decimal("0")
    for rate in sorted(minutes_by_rate):
        amount = pounds(Decimal(minutes_by_rate[rate]) * rate / 60)
        earnings.append({"label": "Basic pay", "minutes": minutes_by_rate[rate], "rate": str(rate), "amount": str(amount)})
        gross += amount
    store_id = max(store_minutes, key=store_minutes.get) if store_minutes else (held[0]["store_id"] if held else None)
    return {
        "days": days, "held": held, "earnings": earnings, "gross_pay": gross,
        "paid_minutes": sum(row["paid_minutes"] for row in days),
        "unpaid_break_minutes": sum(row["unpaid_break"] for row in days),
        "record_ids": [row["record_id"] for row in days], "store_id": store_id,
    }
