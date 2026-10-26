"""Clocking in and out, and the checks that make time-and-attendance hard to fake.

Clock-in and clock-out
  - The time is the server's. A phone's own clock is never used.
  - You can only clock in for a shift you are rostered on (published rota): from 30 minutes
    before it starts until it ends. No shift, or too early, is refused.
  - One open record per person, so no clocking in twice. Each shift can be clocked once.

Flags (the record is saved, and a manager is asked to look)
  - out_of_area:   further from the shop than its radius
  - poor_accuracy: the phone could not place itself within 100 m, so it cannot be checked
  - no_location:   no location was shared
  - shared_device: the same device was used by someone else in the last 12 hours
  - late_clock_out: clocked out more than 4 hours after the shift ended (probably forgotten)
  Fingerprint device and breaks (see apps.fingerprint):
  - app_instead_of_device: clocked on the phone although the shop has a fingerprint device
  - wrong_store_device:    the fingerprint device is at a different shop from the shift
  - app_device_mismatch:   the phone and the fingerprint device disagree by more than 5 minutes
  - delayed_punch:         the device sent the punch late, so its own clock was used
  - break_not_allowed:     a break on a shift under 6 hours
  - break_hours_differ:    the rota length and the hours worked give different paid break time
  - break_not_ended:       no break-end punch, so the break was closed at clock-out
  Informational only (no review): left_early, manually_edited, manual_entry, break_over, no_break.
  Flags from clock-out are written with an "out:" prefix.

A store with no location set up cannot be checked, so it never raises location flags.
"""
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.employees.models import EmployeeProfile
from apps.employees.services import stores_for_employee_on
from apps.rota.models import Shift, ShiftStatus, WeekStatus
from apps.rota.services import lock_employee, shift_span

from . import alerts
from apps.fingerprint import breaks
from apps.fingerprint.models import ClockDevice

from .geo import distance_m
from .models import AttendanceEdit, AttendanceRecord, ReviewStatus, Source

EARLY_WINDOW = timedelta(minutes=30)
ACCURACY_LIMIT_M = 100
SHARED_DEVICE_WINDOW = timedelta(hours=12)
LATE_CLOCK_OUT_AFTER = timedelta(hours=4)
LEFT_EARLY_BY = timedelta(minutes=15)
MIN_SHIFT_LENGTH = timedelta(minutes=1)
MAX_RECORD_LENGTH = timedelta(hours=20)

REVIEW_FLAGS = {
    "out_of_area", "poor_accuracy", "no_location", "shared_device", "late_clock_out",
    "app_instead_of_device", "wrong_store_device", "app_device_mismatch", "delayed_punch",
    "break_not_allowed", "break_hours_differ", "break_not_ended",
}
OUT = "out:"


class AttendanceError(Exception):
    def __init__(self, code, message, status_code=400):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


def current_time():
    return timezone.now()


def local_naive(moment):
    return timezone.localtime(moment).replace(tzinfo=None)


def base_flag(flag):
    return flag[len(OUT):] if flag.startswith(OUT) else flag


def needs_review(flags):
    return any(base_flag(flag) in REVIEW_FLAGS for flag in flags)


# --- Checks ---------------------------------------------------------------------------------

def assess_location(store, latitude, longitude, accuracy_m):
    """(distance in metres or None, flags) for a clock event."""
    if latitude is None or longitude is None:
        return None, ["no_location"]
    if not (-90 <= float(latitude) <= 90 and -180 <= float(longitude) <= 180):
        raise AttendanceError("invalid_location", "That location is not valid.")
    if accuracy_m is not None and accuracy_m < 0:
        raise AttendanceError("invalid_location", "That location is not valid.")
    if store.latitude is None or store.longitude is None:
        return None, []  # the shop's own location is not set up, so there is nothing to compare
    away = round(distance_m(latitude, longitude, store.latitude, store.longitude))
    flags = []
    if away > store.radius_m:
        flags.append("out_of_area")
    if accuracy_m is None or accuracy_m > ACCURACY_LIMIT_M:
        flags.append("poor_accuracy")
    return away, flags


def store_has_clock_device(store):
    return ClockDevice.objects.filter(store=store, is_active=True).exists()


def device_is_shared(employee, device_id, now):
    if not device_id:
        return False
    since = now - SHARED_DEVICE_WINDOW
    return (
        AttendanceRecord.objects.filter(Q(in_device_id=device_id) | Q(out_device_id=device_id))
        .filter(Q(clock_in_at__gte=since) | Q(clock_out_at__gte=since))
        .exclude(employee=employee)
        .exists()
    )


def find_shift(employee, now):
    """The shift this person is clocking in for, or an AttendanceError saying why there isn't one."""
    here = local_naive(now)
    candidates = Shift.objects.filter(
        employee=employee, status=ShiftStatus.SCHEDULED, rota_week__status=WeekStatus.PUBLISHED,
        date__range=(here.date() - timedelta(days=1), here.date()),
    ).select_related("rota_week__store")
    open_now, upcoming = [], []
    for shift in candidates:
        begins, finishes = shift_span(shift.date, shift.start_time, shift.end_time)
        if begins - EARLY_WINDOW <= here <= finishes:
            open_now.append((begins, shift))
    later = Shift.objects.filter(
        employee=employee, status=ShiftStatus.SCHEDULED, rota_week__status=WeekStatus.PUBLISHED, date=here.date(),
    )
    for shift in later:
        begins, _ = shift_span(shift.date, shift.start_time, shift.end_time)
        if begins - EARLY_WINDOW > here:
            upcoming.append(begins)

    unworked = [(b, s) for b, s in open_now if not AttendanceRecord.objects.filter(shift=s).exists()]
    if unworked:
        return min(unworked, key=lambda pair: abs(pair[0] - here))[1]
    if open_now:
        raise AttendanceError("already_worked", "You have already clocked in for this shift.", 409)
    if upcoming:
        start = min(upcoming)
        raise AttendanceError(
            "too_early", f"Your shift starts at {start:%H:%M}. You can clock in from 30 minutes before.", 409,
        )
    raise AttendanceError("no_shift", "You are not rostered to work right now.", 409)


# --- Clocking -------------------------------------------------------------------------------

def _check_employed(employee, now):
    day = local_naive(now).date()
    if (
        not employee.user.is_active
        or day < employee.start_date
        or (employee.end_date is not None and day > employee.end_date)
    ):
        raise AttendanceError("not_employed", "You are not currently employed here.", 403)


@transaction.atomic
def clock_in(*, employee, latitude=None, longitude=None, accuracy_m=None, device_id="", now=None, via=None,
             extra_flags=()):
    """`via` is the fingerprint device when the punch came from one: then there is no phone location to check."""
    now = now or current_time()
    lock_employee(employee)
    _check_employed(employee, now)
    if AttendanceRecord.objects.filter(employee=employee, clock_out_at__isnull=True).exists():
        raise AttendanceError("already_clocked_in", "You are already clocked in.", 409)

    shift = find_shift(employee, now)
    store = shift.rota_week.store
    if via is not None:
        distance, flags, source = None, [], Source.FINGERPRINT
        if via.store_id != store.pk:
            flags.append("wrong_store_device")
    else:
        distance, flags, source = assess_location(store, latitude, longitude, accuracy_m) + (Source.DEVICE,)
        if device_is_shared(employee, device_id, now):
            flags.append("shared_device")
        if store_has_clock_device(store):
            flags.append("app_instead_of_device")
    flags += [flag for flag in extra_flags if flag not in flags]
    begins, _ = shift_span(shift.date, shift.start_time, shift.end_time)
    late = max(0, int((local_naive(now) - begins).total_seconds() // 60))
    record = AttendanceRecord.objects.create(
        employee=employee, store=store, shift=shift, clock_in_at=now,
        in_latitude=latitude, in_longitude=longitude, in_accuracy_m=accuracy_m,
        in_distance_m=distance, in_device_id=device_id, late_minutes=late, flags=flags, source=source,
        review_status=ReviewStatus.PENDING if needs_review(flags) else ReviewStatus.NOT_REQUIRED,
    )
    if record.review_status == ReviewStatus.PENDING:
        alerts.review_needed(record, "in")
    return record


@transaction.atomic
def clock_out(*, employee, latitude=None, longitude=None, accuracy_m=None, device_id="", now=None, via=None,
              extra_flags=()):
    now = now or current_time()
    lock_employee(employee)
    record = (
        AttendanceRecord.objects.select_for_update(of=("self",))
        .select_related("store", "shift")
        .filter(employee=employee, clock_out_at__isnull=True)
        .first()
    )
    if record is None:
        raise AttendanceError("not_clocked_in", "You are not clocked in.", 409)
    if now - record.clock_in_at < MIN_SHIFT_LENGTH:
        raise AttendanceError("too_soon", "You only just clocked in. Please wait a moment.", 409)

    if via is not None:
        distance, flags = None, []
    else:
        distance, flags = assess_location(record.store, latitude, longitude, accuracy_m)
        flags = [OUT + flag for flag in flags]
        if device_is_shared(employee, device_id, now):
            flags.append(OUT + "shared_device")
        if store_has_clock_device(record.store):
            flags.append(OUT + "app_instead_of_device")
    flags += [flag for flag in extra_flags if flag not in flags]
    flags += breaks.finish(record, now)
    if record.shift is not None:
        _, finishes = shift_span(record.shift.date, record.shift.start_time, record.shift.end_time)
        here = local_naive(now)
        if here > finishes + LATE_CLOCK_OUT_AFTER:
            flags.append("late_clock_out")
        elif here < finishes - LEFT_EARLY_BY:
            flags.append("left_early")

    record.clock_out_at = now
    record.out_latitude, record.out_longitude = latitude, longitude
    record.out_accuracy_m, record.out_distance_m, record.out_device_id = accuracy_m, distance, device_id
    record.flags = list(dict.fromkeys([*record.flags, *flags]))
    newly_flagged = needs_review(record.flags) and record.review_status == ReviewStatus.NOT_REQUIRED
    if newly_flagged:
        record.review_status = ReviewStatus.PENDING
    record.save()
    if newly_flagged:  # managers were already told if the clock-in was flagged
        alerts.review_needed(record, "out")
    return record


def open_record(employee):
    return AttendanceRecord.objects.filter(employee=employee, clock_out_at__isnull=True).select_related("store").first()


# --- Manager decisions ----------------------------------------------------------------------

def _fail(code, message):
    from rest_framework.exceptions import ValidationError

    raise ValidationError({code: message})


@transaction.atomic
def review_record(*, record, by, approve, note=""):
    record = AttendanceRecord.objects.select_for_update(of=("self",)).get(pk=record.pk)
    if record.review_status != ReviewStatus.PENDING:
        _fail("review_status", "This entry is not waiting for a decision.")
    if not approve and not note.strip():
        _fail("note", "Say why, so the employee understands.")
    record.review_status = ReviewStatus.APPROVED if approve else ReviewStatus.REJECTED
    record.reviewed_by, record.reviewed_at, record.review_note = by, timezone.now(), note.strip()[:300]
    record.save()
    return record


def _check_times(employee, clock_in_at, clock_out_at, now, exclude_id=None):
    if clock_in_at > now or (clock_out_at is not None and clock_out_at > now):
        _fail("clock_in_at", "Times cannot be in the future.")
    if clock_out_at is not None:
        if clock_out_at <= clock_in_at:
            _fail("clock_out_at", "The clock-out time must be after the clock-in time.")
        if clock_out_at - clock_in_at > MAX_RECORD_LENGTH:
            _fail("clock_out_at", "A record cannot be longer than 20 hours.")
    others = AttendanceRecord.objects.filter(employee=employee).exclude(pk=exclude_id)
    ends = clock_out_at or now
    for other in others:
        other_end = other.clock_out_at or now
        if clock_in_at < other_end and other.clock_in_at < ends:
            _fail("clock_in_at", "This overlaps another attendance record for the same person.")


def _iso(value):
    return value.isoformat() if value is not None else None


@transaction.atomic
def edit_record(*, record, by, reason, clock_in_at=None, clock_out_at=None, now=None):
    now = now or current_time()
    if not reason.strip():
        _fail("reason", "A reason is required for every change.")
    lock_employee(record.employee)
    record = AttendanceRecord.objects.select_for_update(of=("self",)).get(pk=record.pk)
    new_in = clock_in_at or record.clock_in_at
    new_out = clock_out_at or record.clock_out_at
    if new_in == record.clock_in_at and new_out == record.clock_out_at:
        _fail("reason", "Nothing was changed.")
    _check_times(record.employee, new_in, new_out, now, exclude_id=record.pk)

    changes = {}
    if new_in != record.clock_in_at:
        changes["clock_in_at"] = [_iso(record.clock_in_at), _iso(new_in)]
    if new_out != record.clock_out_at:
        changes["clock_out_at"] = [_iso(record.clock_out_at), _iso(new_out)]
    record.clock_in_at, record.clock_out_at = new_in, new_out
    if "manually_edited" not in record.flags:
        record.flags = [*record.flags, "manually_edited"]
    record.save()
    AttendanceEdit.objects.create(record=record, edited_by=by, reason=reason.strip()[:300], changes=changes)
    return record


@transaction.atomic
def add_manual_record(*, by, employee, store, clock_in_at, clock_out_at, reason, shift=None, now=None):
    """A missed clock-in (a phone that died, a forgotten tap). Needs both times and a reason."""
    now = now or current_time()
    if not reason.strip():
        _fail("reason", "A reason is required.")
    lock_employee(employee)
    day = local_naive(clock_in_at).date()
    if not stores_for_employee_on(employee, day).filter(pk=store.pk).exists():
        _fail("store", "This person is not assigned to work at this store on that date.")
    if shift is not None and (shift.employee_id != employee.pk or shift.rota_week.store_id != store.pk):
        _fail("shift", "That shift does not belong to this person at this store.")
    _check_times(employee, clock_in_at, clock_out_at, now)
    record = AttendanceRecord.objects.create(
        employee=employee, store=store, shift=shift, clock_in_at=clock_in_at, clock_out_at=clock_out_at,
        source=Source.MANUAL, flags=["manual_entry"], review_status=ReviewStatus.NOT_REQUIRED,
    )
    AttendanceEdit.objects.create(
        record=record, edited_by=by, reason=reason.strip()[:300],
        changes={"created": [None, {"clock_in_at": _iso(clock_in_at), "clock_out_at": _iso(clock_out_at)}]},
    )
    return record


def purge_locations(older_than_days, now=None):
    """Clear raw coordinates from old records. Distances and flags are kept as evidence."""
    cutoff = (now or current_time()) - timedelta(days=older_than_days)
    return AttendanceRecord.objects.filter(clock_in_at__lt=cutoff).update(
        in_latitude=None, in_longitude=None, out_latitude=None, out_longitude=None
    )
