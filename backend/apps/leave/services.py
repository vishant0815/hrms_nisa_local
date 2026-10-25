"""Requesting, approving and cancelling leave.

Rules:
  - annual leave and unpaid leave must start in the future; "other" leave may start today;
    sickness is reported for today or up to 14 days back, and is recorded straight away
  - a request stays inside one leave year and one person cannot hold two requests on the same date
  - annual leave comes off the allowance. Requests waiting for a decision count against it, so
    nobody can ask for more than they have; the manager may charge fewer days than asked for
  - a manager cannot approve while the person still has shifts on those dates: the shifts have to
    be moved or cancelled first, so the rota never says someone works while they are away
  - nobody decides their own request, and a manager's leave is decided by the Owner or HR
  - once approved, the days are blocked on the rota (see rota.services.evaluate_assignment)
"""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.accounts.models import Role
from apps.rota.models import Shift, ShiftStatus
from apps.rota.services import lock_employee, problem, raise_blocks

from . import alerts
from .models import LIVE_STATUSES, LeaveAllowance, LeaveKind, LeaveRequest, LeaveStatus
from .rules import HALF, days_in_range, default_allowance, leave_year, number

MAX_DAYS = 60
SICK_BACKDATE_DAYS = 14
DECIDERS = (Role.OWNER, Role.HR, Role.MANAGER)
ADMINS = (Role.OWNER, Role.HR)


def today():
    return timezone.localdate()


def bad(field, message):
    raise ValidationError({field: message})


# --- Balance --------------------------------------------------------------------------------

def allowance_for(employee, on):
    first, last = leave_year(on)
    row = LeaveAllowance.objects.filter(employee=employee, year_start=first).first()
    return row.days if row else default_allowance(employee, first, last)


def balance(employee, on):
    """Annual-leave days for the leave year containing `on`."""
    first, last = leave_year(on)
    annual = LeaveRequest.objects.filter(employee=employee, kind=LeaveKind.ANNUAL, start_date__range=(first, last))
    zero = Decimal("0")
    allowance = allowance_for(employee, on)
    used = annual.filter(status=LeaveStatus.APPROVED).aggregate(t=Sum("days_charged"))["t"] or zero
    waiting = annual.filter(status=LeaveStatus.PENDING).aggregate(t=Sum("days_requested"))["t"] or zero
    return {
        "year_start": first, "year_end": last, "allowance": allowance, "used": used, "waiting": waiting,
        "remaining": allowance - used - waiting,
    }


@transaction.atomic
def set_allowance(*, employee, on, days, by):
    if days < 0 or days > 366 or days % HALF != 0:
        bad("days", "Use a number between 0 and 366, in half-day steps.")
    first, _ = leave_year(on)
    LeaveAllowance.objects.update_or_create(
        employee=employee, year_start=first, defaults={"days": days, "set_by": by},
    )
    return balance(employee, on)


# --- Helpers --------------------------------------------------------------------------------

def scheduled_shifts(employee, start, end):
    return (
        Shift.objects.filter(employee=employee, status=ShiftStatus.SCHEDULED, date__range=(start, end))
        .select_related("rota_week__store")
        .order_by("date", "start_time", "id")
    )


def _lock(leave):
    return LeaveRequest.objects.select_for_update().select_related("employee__user").get(pk=leave.pk)


def _require_decider(leave, by):
    if by.role not in DECIDERS:
        raise PermissionDenied()
    if leave.employee.user_id == by.pk:
        raise ValidationError("You can't decide your own leave request.")
    if leave.employee.user.role == Role.MANAGER and by.role == Role.MANAGER:
        raise ValidationError("A manager's leave is decided by the Owner or HR.")


def _finish(leave, by, status, note=""):
    leave.status = status
    leave.reviewed_by = by
    leave.reviewed_at = timezone.now()
    leave.decision_note = note[:300]


# --- Requesting -----------------------------------------------------------------------------

@transaction.atomic
def request_leave(*, employee, kind, start_date, end_date=None, half_day="", note=""):
    lock_employee(employee)
    end_date = end_date or start_date
    now = today()

    if end_date < start_date:
        bad("end_date", "The end date cannot be before the start date.")
    if half_day and start_date != end_date:
        bad("half_day", "A half day can only be asked for on a single date.")
    if (end_date - start_date).days + 1 > MAX_DAYS:
        bad("end_date", f"Leave can be at most {MAX_DAYS} days in one request. Please split it.")
    if start_date < employee.start_date or (employee.end_date and end_date > employee.end_date):
        bad("start_date", "Leave has to fall within your employment dates.")
    if leave_year(start_date) != leave_year(end_date):
        bad("end_date", "A request can't cross into the next leave year. Please send two requests.")

    if kind == LeaveKind.SICK:
        if start_date > now:
            bad("start_date", "Sickness can only be reported from today or earlier.")
        if start_date < now - timedelta(days=SICK_BACKDATE_DAYS):
            bad("start_date", f"That is more than {SICK_BACKDATE_DAYS} days ago. Please contact HR.")
        if end_date > now + timedelta(days=SICK_BACKDATE_DAYS):
            bad("end_date", f"Report up to {SICK_BACKDATE_DAYS} days ahead and add more later if you need to.")
    elif start_date < now or (start_date == now and kind != LeaveKind.OTHER):
        bad("start_date", "Leave has to start in the future.")

    if LeaveRequest.objects.filter(
        employee=employee, status__in=LIVE_STATUSES, start_date__lte=end_date, end_date__gte=start_date,
    ).exists():
        raise ValidationError("You already have leave requested or booked on some of those dates.")

    days = days_in_range(start_date, end_date, half_day)
    if kind == LeaveKind.ANNUAL:
        remaining = balance(employee, start_date)["remaining"]
        if days > remaining:
            raise ValidationError(
                f"You have {number(max(remaining, Decimal('0')))} days of annual leave left in this leave year, "
                "counting requests still waiting for a decision."
            )

    sick = kind == LeaveKind.SICK
    leave = LeaveRequest.objects.create(
        employee=employee, kind=kind, start_date=start_date, end_date=end_date, half_day=half_day,
        days_requested=days, note=note.strip()[:300],
        status=LeaveStatus.APPROVED if sick else LeaveStatus.PENDING,
        reviewed_at=timezone.now() if sick else None,
    )
    if sick:
        alerts.sick_reported(leave, list(scheduled_shifts(employee, start_date, end_date)))
    else:
        alerts.leave_requested(leave)
    return leave


# --- Deciding -------------------------------------------------------------------------------

@transaction.atomic
def approve(*, leave, by, days_charged=None):
    leave = _lock(leave)
    lock_employee(leave.employee)
    _require_decider(leave, by)
    if leave.status != LeaveStatus.PENDING:
        raise ValidationError("This request has already been dealt with.")
    if leave.start_date < today():
        raise ValidationError(
            "This leave has already started, so it can't be approved. Reject it and ask for a new request."
        )

    if not leave.half_day:
        clash = list(scheduled_shifts(leave.employee, leave.start_date, leave.end_date))
        if clash:
            where = ", ".join(f"{s.date:%a %d %b} at {s.rota_week.store.name}" for s in clash[:6])
            more = f" and {len(clash) - 6} more" if len(clash) > 6 else ""
            raise_blocks([problem(
                "has_shifts",
                f"This person is on the rota on those dates ({where}{more}). Move or cancel those shifts first.",
            )])

    if leave.kind == LeaveKind.ANNUAL:
        charged = leave.days_requested if days_charged is None else Decimal(str(days_charged))
        if charged <= 0 or charged > leave.days_requested or charged % HALF != 0:
            bad("days_charged", "Use half-day steps, more than 0 and no more than the days requested.")
        info = balance(leave.employee, leave.start_date)
        left = info["allowance"] - info["used"]
        if charged > left:
            raise ValidationError(f"Only {number(max(left, Decimal('0')))} days of annual leave are left for this person.")
        leave.days_charged = charged
    elif days_charged is not None:
        bad("days_charged", "Only annual leave uses the allowance.")

    _finish(leave, by, LeaveStatus.APPROVED)
    leave.save()
    alerts.leave_decided(leave)
    return leave


@transaction.atomic
def reject(*, leave, by, reason):
    leave = _lock(leave)
    _require_decider(leave, by)
    if leave.status != LeaveStatus.PENDING:
        raise ValidationError("This request has already been dealt with.")
    if not reason.strip():
        bad("reason", "Say why, so the person knows what to do next.")
    _finish(leave, by, LeaveStatus.REJECTED, reason.strip())
    leave.save()
    alerts.leave_decided(leave)
    return leave


# --- Cancelling -----------------------------------------------------------------------------

@transaction.atomic
def cancel_own(*, leave, user):
    leave = _lock(leave)
    if leave.employee.user_id != user.pk:
        raise PermissionDenied()
    if leave.status not in LIVE_STATUSES:
        raise ValidationError("This request is already closed.")
    if leave.kind == LeaveKind.SICK:
        raise ValidationError("Sickness reports can't be cancelled here. Please ask HR to correct it.")
    was_approved = leave.status == LeaveStatus.APPROVED
    if was_approved and leave.start_date <= today():
        raise ValidationError("This leave has already started. Please speak to your manager.")
    leave.status = LeaveStatus.CANCELLED
    leave.save()
    if was_approved:
        alerts.leave_cancelled_by_employee(leave)
    return leave


@transaction.atomic
def cancel_by_manager(*, leave, by, reason):
    leave = _lock(leave)
    _require_decider(leave, by)
    if leave.status not in LIVE_STATUSES:
        raise ValidationError("This request is already closed.")
    if not reason.strip():
        bad("reason", "Say why, so the person knows what happened.")
    if by.role not in ADMINS and leave.start_date <= today():
        raise ValidationError("Only the Owner or HR can cancel leave that has already started.")
    _finish(leave, by, LeaveStatus.CANCELLED, reason.strip())
    leave.save()
    alerts.leave_cancelled_by_manager(leave)
    return leave
