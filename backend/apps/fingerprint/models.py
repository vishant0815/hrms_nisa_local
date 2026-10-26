"""Fingerprint clock devices, who is enrolled on them, and what they sent.

No fingerprint or template is ever stored here. The device keeps those. We keep only which
device-user number belongs to which employee, and a log of the punches the devices send.
"""
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

ONLINE_WITHIN = timedelta(minutes=10)


class ClockDevice(models.Model):
    store = models.ForeignKey("stores.Store", on_delete=models.PROTECT, related_name="clock_devices")
    name = models.CharField(max_length=100)
    serial = models.CharField(max_length=64, unique=True)
    secret_hash = models.CharField(max_length=64, editable=False, help_text="SHA-256 of the secret. The secret is shown once.")
    is_active = models.BooleanField(default=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    clock_skew_seconds = models.IntegerField(null=True, blank=True, help_text="Server time minus device time at the last contact.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["store_id", "name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.store})"

    @property
    def online(self) -> bool:
        return bool(self.last_seen_at and timezone.now() - self.last_seen_at <= ONLINE_WITHIN)


class Enrolment(models.Model):
    """Which device-user number is this employee. The same number is used on every device."""

    employee = models.ForeignKey("employees.EmployeeProfile", on_delete=models.PROTECT, related_name="fingerprint_enrolments")
    device_user_id = models.PositiveIntegerField(unique=True, help_text="Never reused, even after withdrawal.")
    consent_note = models.CharField(max_length=300, help_text="Where the signed consent is kept.")
    consented_at = models.DateTimeField(default=timezone.now)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    withdrawn_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    withdrawn_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["device_user_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["employee"], condition=Q(withdrawn_at__isnull=True), name="one_active_enrolment_per_employee",
            ),
        ]

    @property
    def active(self) -> bool:
        return self.withdrawn_at is None


class PunchKind(models.TextChoices):
    IN = "in", "Clock in"
    OUT = "out", "Clock out"
    BREAK_START = "break_start", "Break start"
    BREAK_END = "break_end", "Break end"


class PunchOutcome(models.TextChoices):
    ACCEPTED = "accepted", "Accepted"
    DUPLICATE = "duplicate", "Already known"
    REJECTED = "rejected", "Rejected"


class Punch(models.Model):
    """Everything a device sent, including what was refused and why. Added to, never changed afterwards."""

    device = models.ForeignKey(ClockDevice, on_delete=models.PROTECT, related_name="punches")
    device_user_id = models.PositiveIntegerField()
    kind = models.CharField(max_length=12, choices=PunchKind.choices)
    device_time = models.DateTimeField(help_text="What the device's own clock said.")
    received_at = models.DateTimeField(help_text="Our clock when it arrived.")
    effective_at = models.DateTimeField(null=True, blank=True, help_text="The time the record uses.")
    employee = models.ForeignKey("employees.EmployeeProfile", null=True, blank=True, on_delete=models.PROTECT, related_name="punches")
    record = models.ForeignKey("attendance.AttendanceRecord", null=True, blank=True, on_delete=models.PROTECT, related_name="punches")
    outcome = models.CharField(max_length=10, choices=PunchOutcome.choices, default=PunchOutcome.ACCEPTED)
    reason = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_at", "-id"]
        indexes = [models.Index(fields=["device", "received_at"], name="punch_device_time_idx")]
        constraints = [
            models.UniqueConstraint(
                fields=["device", "device_user_id", "kind", "device_time"], name="one_punch_per_device_event",
            ),
        ]


class BreakPeriod(models.Model):
    record = models.ForeignKey("attendance.AttendanceRecord", on_delete=models.PROTECT, related_name="breaks")
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["started_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["record"], condition=Q(ended_at__isnull=True), name="one_open_break_per_record"),
            models.CheckConstraint(
                condition=Q(ended_at__isnull=True) | Q(ended_at__gt=F("started_at")), name="break_ends_after_start",
            ),
        ]
