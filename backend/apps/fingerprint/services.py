"""Turning what a fingerprint device sends into attendance records and breaks.

A device sends four kinds of punch: in, out, break start, break end.

Time
  - Normally the record uses OUR clock when the punch arrives, not the device's.
  - A device may hold punches while it is offline and send them later. If the device's own time
    is more than 5 minutes older than the arrival (and under 24 hours), the device's time is used
    and the record is flagged `delayed_punch` for a manager to confirm.
  - A device time in the future, or more than 24 hours old, is refused (`device_clock_wrong`).

Safety
  - A device must be registered to a store and present its secret. Unknown devices are refused.
  - Only enrolled people count. Someone who has withdrawn their consent is refused.
  - A repeat of the same punch (devices retry) changes nothing. A second punch of the same kind
    within 60 seconds (a double tap) is ignored.
  - Everything is logged in Punch, including what was refused and why, so nothing vanishes.

The rules for who may clock in, late minutes and so on are the shared ones in attendance.services.
"""
import hashlib
import hmac
import secrets
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.attendance import alerts as attendance_alerts
from apps.attendance import services as attendance
from apps.attendance.models import AttendanceEdit, AttendanceRecord, ReviewStatus, Source
from apps.employees.models import EmployeeProfile
from apps.rota.services import lock_employee

from .models import BreakPeriod, ClockDevice, Enrolment, Punch, PunchKind, PunchOutcome

TOLERANCE = timedelta(minutes=5)
MAX_DELAY = timedelta(hours=24)
DUPLICATE_WINDOW = timedelta(seconds=60)
MISMATCH = timedelta(minutes=5)


# --- Devices --------------------------------------------------------------------------------

def digest(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


def new_secret():
    """(secret to show once, hash to keep)."""
    secret = secrets.token_urlsafe(32)
    return secret, digest(secret)


def authenticate(serial, secret):
    """The active device with this serial and secret, or None."""
    device = ClockDevice.objects.filter(serial=serial, is_active=True).select_related("store").first()
    if device is None:
        digest(secret or "")  # similar work whether or not the serial exists
        return None
    return device if hmac.compare_digest(device.secret_hash, digest(secret or "")) else None


def note_contact(device, device_time, now):
    ClockDevice.objects.filter(pk=device.pk).update(
        last_seen_at=now, clock_skew_seconds=int((now - device_time).total_seconds()),
    )


@transaction.atomic
def register_device(*, store, name, serial):
    if ClockDevice.objects.filter(serial=serial).exists():
        raise ValidationError({"serial": "A device with this serial is already registered."})
    secret, hashed = new_secret()
    device = ClockDevice.objects.create(store=store, name=name, serial=serial, secret_hash=hashed)
    return device, secret


@transaction.atomic
def rotate_secret(device):
    secret, hashed = new_secret()
    ClockDevice.objects.filter(pk=device.pk).update(secret_hash=hashed)
    return secret


# --- Enrolment ------------------------------------------------------------------------------

@transaction.atomic
def enrol(*, employee, by, consent_note):
    if not consent_note.strip():
        raise ValidationError({"consent_note": "Say where the signed consent is kept."})
    lock_employee(employee)
    if Enrolment.objects.filter(employee=employee, withdrawn_at__isnull=True).exists():
        raise ValidationError("This person is already enrolled.")
    last = Enrolment.objects.order_by("-device_user_id").values_list("device_user_id", flat=True).first() or 0
    return Enrolment.objects.create(
        employee=employee, device_user_id=last + 1, consent_note=consent_note.strip()[:300], recorded_by=by,
    )


@transaction.atomic
def withdraw(*, enrolment, by, reason):
    enrolment = Enrolment.objects.select_for_update().get(pk=enrolment.pk)
    if enrolment.withdrawn_at is not None:
        raise ValidationError("This enrolment has already been withdrawn.")
    enrolment.withdrawn_at, enrolment.withdrawn_by = timezone.now(), by
    enrolment.withdrawn_reason = reason.strip()[:300]
    enrolment.save()
    return enrolment


# --- Punches --------------------------------------------------------------------------------

def add_flags(record, flags, event="in"):
    """Add flags to a record after the fact, and ask the managers to look if they need a decision."""
    record = AttendanceRecord.objects.select_for_update().select_related("store", "employee__user").get(pk=record.pk)
    new = [flag for flag in flags if flag not in record.flags]
    if not new:
        return record
    record.flags = [*record.flags, *new]
    newly = attendance.needs_review(record.flags) and record.review_status == ReviewStatus.NOT_REQUIRED
    if newly:
        record.review_status = ReviewStatus.PENDING
    record.save()
    if newly:
        attendance_alerts.review_needed(record, event)
    return record


def effective_time(received_at, device_time):
    """(the time to record, whether the device's own time was used)."""
    gap = received_at - device_time
    if abs(gap) <= TOLERANCE:
        return received_at, False
    if TOLERANCE < gap <= MAX_DELAY:
        return device_time, True
    raise attendance.AttendanceError("device_clock_wrong", "The device's time is not usable.")


def _open_break(record):
    return record.breaks.filter(ended_at__isnull=True).first()


def _need_open_record(employee):
    record = attendance.open_record(employee)
    if record is None:
        raise attendance.AttendanceError("not_clocked_in", "Not clocked in.", 409)
    return record


def _punch_in(employee, device, when, delayed):
    attendance._check_employed(employee, when)
    open_record = attendance.open_record(employee)
    if open_record is not None:
        if open_record.source != Source.FINGERPRINT:
            # Already clocked in on the app. Not a second record: the two must agree.
            if abs(when - open_record.clock_in_at) > MISMATCH:
                add_flags(open_record, ["app_device_mismatch"], "in")
            return open_record, PunchOutcome.DUPLICATE, "confirms_app_clock_in"
        raise attendance.AttendanceError("already_clocked_in", "Already clocked in.", 409)
    record = attendance.clock_in(employee=employee, via=device, now=when, extra_flags=["delayed_punch"] if delayed else [])
    return record, PunchOutcome.ACCEPTED, ""


def _punch_out(employee, device, when, delayed):
    record = attendance.clock_out(employee=employee, via=device, now=when, extra_flags=["delayed_punch"] if delayed else [])
    return record, PunchOutcome.ACCEPTED, ""


def _punch_break_start(employee, device, when, delayed):
    lock_employee(employee)
    record = _need_open_record(employee)
    if _open_break(record) is not None:
        raise attendance.AttendanceError("already_on_break", "Already on a break.", 409)
    if when <= record.clock_in_at:
        raise attendance.AttendanceError("bad_order", "That is before the clock-in.", 409)
    BreakPeriod.objects.create(record=record, started_at=when)
    if delayed:
        record = add_flags(record, ["delayed_punch"])
    return record, PunchOutcome.ACCEPTED, ""


def _punch_break_end(employee, device, when, delayed):
    lock_employee(employee)
    record = _need_open_record(employee)
    period = _open_break(record)
    if period is None:
        raise attendance.AttendanceError("not_on_break", "Not on a break.", 409)
    if when <= period.started_at:
        raise attendance.AttendanceError("bad_order", "That is before the break started.", 409)
    period.ended_at = when
    period.save(update_fields=["ended_at"])
    if delayed:
        record = add_flags(record, ["delayed_punch"])
    return record, PunchOutcome.ACCEPTED, ""


HANDLERS = {
    PunchKind.IN: _punch_in,
    PunchKind.OUT: _punch_out,
    PunchKind.BREAK_START: _punch_break_start,
    PunchKind.BREAK_END: _punch_break_end,
}


def _decide(punch, device, received_at):
    """Work out what a logged punch means. Returns (record, outcome, reason)."""
    enrolment = Enrolment.objects.select_related("employee__user").filter(device_user_id=punch.device_user_id).first()
    if enrolment is None:
        return None, PunchOutcome.REJECTED, "unknown_user"
    employee = enrolment.employee
    punch.employee = employee
    if not enrolment.active:
        return None, PunchOutcome.REJECTED, "withdrawn"

    try:
        when, delayed = effective_time(received_at, punch.device_time)
    except attendance.AttendanceError as error:
        return None, PunchOutcome.REJECTED, error.code
    punch.effective_at = when

    recent = Punch.objects.filter(
        employee=employee, kind=punch.kind, outcome=PunchOutcome.ACCEPTED,
        effective_at__gt=when - DUPLICATE_WINDOW, effective_at__lte=when + DUPLICATE_WINDOW,
    ).exclude(pk=punch.pk)
    if recent.exists():
        return None, PunchOutcome.DUPLICATE, "double_tap"

    try:
        with transaction.atomic():
            return HANDLERS[punch.kind](employee, device, when, delayed)
    except attendance.AttendanceError as error:
        return None, PunchOutcome.REJECTED, error.code


@transaction.atomic
def process_punch(*, device, user_id, kind, device_time, received_at=None):
    """Handle one punch from a device. Safe to send twice: the second time returns the first result."""
    received_at = received_at or attendance.current_time()
    try:
        with transaction.atomic():
            punch = Punch.objects.create(
                device=device, device_user_id=user_id, kind=kind, device_time=device_time, received_at=received_at,
            )
    except IntegrityError:
        return Punch.objects.get(device=device, device_user_id=user_id, kind=kind, device_time=device_time)

    note_contact(device, device_time, received_at)
    record, outcome, reason = _decide(punch, device, received_at)
    punch.record, punch.outcome, punch.reason = record, outcome, reason
    punch.save()
    return punch


# --- Break decisions ------------------------------------------------------------------------

@transaction.atomic
def set_break_basis(*, record, by, basis, reason):
    """A manager decides whether the rota length or the hours worked decides the break allowance."""
    if basis not in ("rota", "actual"):
        raise ValidationError({"basis": "Use rota or actual."})
    if not reason.strip():
        raise ValidationError({"reason": "A reason is required for every change."})
    record = AttendanceRecord.objects.select_for_update().get(pk=record.pk)
    before = record.break_basis or "actual"
    if before == basis:
        raise ValidationError({"basis": "That is already the choice."})
    record.break_basis = basis
    record.save(update_fields=["break_basis", "updated_at"])
    AttendanceEdit.objects.create(
        record=record, edited_by=by, reason=reason.strip()[:300], changes={"break_basis": [before, basis]},
    )
    return record
