"""Building the rota, and the rules that stop a manager breaking them.

BLOCKS (the shift is refused):
  - the person is not employed on that date
  - the person is not assigned to work at this store on that date (home store or approved cover)
  - the person is not cleared to work, or the week would exceed their visa hours cap
  - the shift overlaps another of their shifts, at any store
  - the shift itself is malformed, in the past, or longer than 13 hours

WARNINGS (the shift is saved, the manager is told):
  - less than 11 hours' rest since or before another shift
  - more than 48 hours planned in the week
  - outside the store's opening hours, or on a day it is closed

Messages shown to managers never reveal immigration details: they only say "not cleared" or
quote the weekly hours limit. HR can see the reason on the visa pages.
"""
from datetime import datetime, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.employees.models import EmployeeProfile
from apps.employees.services import stores_for_employee_on
from apps.stores.models import StoreHours
from apps.visa.rules import Code, check_right_to_work, week_bounds

from .models import RotaWeek, Shift, ShiftStatus, WeekStatus

MIN_REST = timedelta(hours=11)
MAX_SHIFT = timedelta(hours=13)  # longer cannot leave 11 hours' rest in a 24-hour period
WEEKLY_WARNING_HOURS = 48


def problem(code, message, **extra):
    return {"code": code, "message": message, **extra}


def raise_blocks(blocks):
    raise ValidationError({"blocks": blocks})


# --- Time maths -----------------------------------------------------------------------------

def shift_span(day, start, end):
    """(start, end) datetimes. A shift whose end is not after its start runs into the next day."""
    begins = datetime.combine(day, start)
    finishes = datetime.combine(day, end)
    if finishes <= begins:
        finishes += timedelta(days=1)
    return begins, finishes


def shift_hours(day, start, end, break_minutes) -> Decimal:
    begins, finishes = shift_span(day, start, end)
    minutes = int((finishes - begins).total_seconds() // 60) - break_minutes
    return (Decimal(minutes) / Decimal(60)).quantize(Decimal("0.01"))


# --- Rules ----------------------------------------------------------------------------------

def shape_blocks(rota_week, day, start, end, break_minutes):
    """Problems with the shift itself, whoever works it."""
    blocks = []
    today = timezone.localdate()
    week_end = rota_week.week_start + timedelta(days=6)
    if not rota_week.week_start <= day <= week_end:
        blocks.append(problem("date_outside_week", "The date is outside this rota week."))
    if day < today:
        blocks.append(problem("shift_in_past", "Shifts in the past cannot be created or changed."))
    if not rota_week.store.is_active:
        blocks.append(problem("store_inactive", "This store is not active."))
    if start == end:
        blocks.append(problem("no_length", "The start and end times cannot be the same."))
        return blocks
    begins, finishes = shift_span(day, start, end)
    length = finishes - begins
    if length > MAX_SHIFT:
        blocks.append(problem("shift_too_long", "A shift cannot be longer than 13 hours."))
    if timedelta(minutes=break_minutes) >= length:
        blocks.append(problem("break_too_long", "The break must be shorter than the shift."))
    return blocks


def opening_hours_warnings(store, day, start, end):
    hours = StoreHours.objects.filter(store=store, weekday=day.weekday()).first()
    if hours is None:
        return []  # opening hours not set up for this day: nothing to compare with
    if hours.is_closed:
        return [problem("store_closed", "The store is marked closed on this day.")]
    opens = hours.opens.hour * 60 + hours.opens.minute
    closes = hours.closes.hour * 60 + hours.closes.minute
    if closes <= opens:
        closes += 24 * 60
    begins = start.hour * 60 + start.minute
    finishes = end.hour * 60 + end.minute
    if finishes <= begins:
        finishes += 24 * 60
    if begins < opens or finishes > closes:
        return [problem("outside_opening_hours", "The shift is outside the store's opening hours.")]
    return []


def evaluate_assignment(*, store, employee, day, start, end, break_minutes, exclude_shift_id=None):
    """Check putting `employee` on this shift. Returns (blocks, warnings), both lists of dicts."""
    blocks, warnings = [], []

    if (
        day < employee.start_date
        or (employee.end_date is not None and day > employee.end_date)
        or not employee.user.is_active
    ):
        return [problem("not_employed", "This person is not employed on that date.")], warnings

    if not stores_for_employee_on(employee, day).filter(pk=store.pk).exists():
        blocks.append(problem(
            "not_assigned_to_store", "This person is not assigned to work at this store on that date.",
        ))

    # Everything else they are rostered for in the surrounding days, at ANY store, drafts included
    # (a draft may be published, so it counts).
    week_start, week_end = week_bounds(day)
    others = [
        other
        for other in Shift.objects.filter(
            employee=employee,
            status=ShiftStatus.SCHEDULED,
            date__range=(week_start - timedelta(days=1), week_end + timedelta(days=1)),
        ).exclude(pk=exclude_shift_id)
    ]
    week_hours = shift_hours(day, start, end, break_minutes) + sum(
        (shift_hours(o.date, o.start_time, o.end_time, o.break_minutes)
         for o in others if week_start <= o.date <= week_end),
        Decimal("0"),
    )

    decision = check_right_to_work(employee, day, week_hours)
    if not decision.allowed:
        if decision.codes == {Code.HOURS_LIMIT}:
            blocks.append(problem(
                "hours_limit", f"Over the weekly hours limit of {decision.weekly_hours_limit}."
            ))
        else:
            blocks.append(problem("not_cleared", "Not cleared to work. Please contact HR."))
    elif decision.paperwork_needed:
        warnings.append(problem("paperwork_needed", "Paperwork needed for this person. Please contact HR."))

    begins, finishes = shift_span(day, start, end)
    overlapped = False
    short_rest = False
    for other in others:
        other_begins, other_finishes = shift_span(other.date, other.start_time, other.end_time)
        if begins < other_finishes and other_begins < finishes:
            overlapped = True
        else:
            gap = begins - other_finishes if other_finishes <= begins else other_begins - finishes
            if gap < MIN_REST:
                short_rest = True
    if overlapped:
        blocks.append(problem("double_booked", "This person already has an overlapping shift."))
    elif short_rest:
        warnings.append(problem("short_rest", "This leaves less than 11 hours' rest between shifts."))

    if week_hours > WEEKLY_WARNING_HOURS:
        warnings.append(problem("long_week", f"This brings the week to {week_hours} hours (over 48)."))
    return blocks, warnings


def lock_employee(employee):
    """Serialise changes for one person so two managers cannot both fit the last allowed hours."""
    if employee is not None:
        EmployeeProfile.objects.select_for_update().filter(pk=employee.pk).first()


# --- Weeks ----------------------------------------------------------------------------------

@transaction.atomic
def create_week(*, store, week_start, by, copy_from_previous=False):
    today = timezone.localdate()
    blocks = []
    if week_start.weekday() != 0:
        blocks.append(problem("week_not_monday", "A rota week must start on a Monday."))
    if week_start + timedelta(days=6) < today:
        blocks.append(problem("week_in_past", "That week has already finished."))
    if not store.is_active:
        blocks.append(problem("store_inactive", "This store is not active."))
    if RotaWeek.objects.filter(store=store, week_start=week_start).exists():
        blocks.append(problem("week_exists", "A rota already exists for this store and week."))
    previous = None
    if copy_from_previous:
        previous = RotaWeek.objects.filter(store=store, week_start=week_start - timedelta(days=7)).first()
        if previous is None:
            blocks.append(problem("no_previous_week", "There is no rota for the previous week to copy."))
    if blocks:
        raise_blocks(blocks)

    week = RotaWeek.objects.create(store=store, week_start=week_start, created_by=by)
    summary = {"copied": 0, "made_open": [], "skipped_past": 0}
    if previous is not None:
        sources = (
            previous.shifts.filter(status=ShiftStatus.SCHEDULED)
            .select_related("employee__user")
            .order_by("date", "start_time", "id")
        )
        for source in sources:
            new_date = source.date + timedelta(days=7)
            if new_date < today:
                summary["skipped_past"] += 1
                continue
            employee = source.employee
            if employee is not None:
                lock_employee(employee)
                issues, _ = evaluate_assignment(
                    store=store, employee=employee, day=new_date, start=source.start_time,
                    end=source.end_time, break_minutes=source.break_minutes,
                )
                if issues:
                    # Keep the shift but leave it open, so the manager can find someone else.
                    summary["made_open"].append({
                        "employee": employee.pk, "date": new_date, "message": issues[0]["message"],
                    })
                    employee = None
            Shift.objects.create(
                rota_week=week, employee=employee, date=new_date, start_time=source.start_time,
                end_time=source.end_time, break_minutes=source.break_minutes, role=source.role,
                created_by=by,
            )
            summary["copied"] += 1
    return week, summary


@transaction.atomic
def publish_week(*, rota_week, by):
    """Make the week visible to staff. Everything still to be worked is re-checked first."""
    if rota_week.status == WeekStatus.PUBLISHED:
        raise_blocks([problem("already_published", "This rota is already published.")])
    today = timezone.localdate()
    blocks = []
    upcoming = (
        rota_week.shifts.filter(status=ShiftStatus.SCHEDULED, employee__isnull=False, date__gte=today)
        .select_related("employee__user")
        .order_by("date", "start_time", "id")
    )
    for shift in upcoming:
        issues, _ = evaluate_assignment(
            store=rota_week.store, employee=shift.employee, day=shift.date, start=shift.start_time,
            end=shift.end_time, break_minutes=shift.break_minutes, exclude_shift_id=shift.pk,
        )
        blocks.extend({**issue, "shift": shift.pk} for issue in issues)
    if blocks:
        raise_blocks(blocks)
    rota_week.status = WeekStatus.PUBLISHED
    rota_week.published_by = by
    rota_week.published_at = timezone.now()
    rota_week.save(update_fields=["status", "published_by", "published_at"])
    return rota_week


# --- Shifts ---------------------------------------------------------------------------------

@transaction.atomic
def create_shift(*, rota_week, by, employee, date, start_time, end_time, break_minutes=0, role="", notes=""):
    lock_employee(employee)
    blocks = shape_blocks(rota_week, date, start_time, end_time, break_minutes)
    warnings = []
    if not blocks and employee is not None:
        blocks, warnings = evaluate_assignment(
            store=rota_week.store, employee=employee, day=date, start=start_time, end=end_time,
            break_minutes=break_minutes,
        )
    if blocks:
        raise_blocks(blocks)
    warnings += opening_hours_warnings(rota_week.store, date, start_time, end_time)
    shift = Shift.objects.create(
        rota_week=rota_week, employee=employee, date=date, start_time=start_time, end_time=end_time,
        break_minutes=break_minutes, role=role, notes=notes, created_by=by,
    )
    return shift, warnings


CHECKED_FIELDS = {"employee", "date", "start_time", "end_time", "break_minutes"}


@transaction.atomic
def update_shift(*, shift, by, **changes):
    """Change a shift. The rules run again only if who, when or how long changed."""
    if shift.status == ShiftStatus.CANCELLED:
        raise_blocks([problem("shift_cancelled", "A cancelled shift cannot be changed.")])
    if shift.date < timezone.localdate():
        raise_blocks([problem("shift_in_past", "Shifts in the past cannot be created or changed.")])

    values = {
        "employee": shift.employee, "date": shift.date, "start_time": shift.start_time,
        "end_time": shift.end_time, "break_minutes": shift.break_minutes,
    }
    rules_changed = any(
        name in changes and changes[name] != values[name] for name in CHECKED_FIELDS
    )
    values.update({name: changes[name] for name in CHECKED_FIELDS if name in changes})

    warnings = []
    if rules_changed:
        lock_employee(values["employee"])
        blocks = shape_blocks(
            shift.rota_week, values["date"], values["start_time"], values["end_time"], values["break_minutes"]
        )
        if not blocks and values["employee"] is not None:
            blocks, warnings = evaluate_assignment(
                store=shift.rota_week.store, employee=values["employee"], day=values["date"],
                start=values["start_time"], end=values["end_time"],
                break_minutes=values["break_minutes"], exclude_shift_id=shift.pk,
            )
        if blocks:
            raise_blocks(blocks)
        warnings += opening_hours_warnings(
            shift.rota_week.store, values["date"], values["start_time"], values["end_time"]
        )

    for name, value in changes.items():
        setattr(shift, name, value)
    shift.save()
    return shift, warnings


@transaction.atomic
def cancel_shift(*, shift, by):
    if shift.status == ShiftStatus.CANCELLED:
        raise_blocks([problem("already_cancelled", "This shift is already cancelled.")])
    if shift.date < timezone.localdate():
        raise_blocks([problem("shift_in_past", "Shifts in the past cannot be created or changed.")])
    shift.status = ShiftStatus.CANCELLED
    shift.save(update_fields=["status", "updated_at"])
    return shift
