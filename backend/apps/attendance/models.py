from django.conf import settings
from django.db import models


class ReviewStatus(models.TextChoices):
    NOT_REQUIRED = "not_required", "Not needed"
    PENDING = "pending", "Waiting for a manager"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class Source(models.TextChoices):
    DEVICE = "device", "Clocked in the app"
    FINGERPRINT = "fingerprint", "Fingerprint device"
    MANUAL = "manual", "Added by a manager"


class AttendanceRecord(models.Model):
    """One period of work: clock-in to clock-out. Times are set by the server, never by the phone.

    Location is kept only to check the person was at the shop. The raw coordinates are cleared
    after a retention period (see purge_attendance_locations); the distance and flags stay as evidence.
    """

    employee = models.ForeignKey("employees.EmployeeProfile", on_delete=models.PROTECT, related_name="attendance")
    store = models.ForeignKey("stores.Store", on_delete=models.PROTECT, related_name="attendance")
    shift = models.ForeignKey("rota.Shift", null=True, blank=True, on_delete=models.PROTECT, related_name="attendance")

    clock_in_at = models.DateTimeField()
    clock_out_at = models.DateTimeField(null=True, blank=True)

    in_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    in_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    in_accuracy_m = models.PositiveIntegerField(null=True, blank=True)
    in_distance_m = models.PositiveIntegerField(null=True, blank=True)
    in_device_id = models.CharField(max_length=64, blank=True)

    out_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    out_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    out_accuracy_m = models.PositiveIntegerField(null=True, blank=True)
    out_distance_m = models.PositiveIntegerField(null=True, blank=True)
    out_device_id = models.CharField(max_length=64, blank=True)

    late_minutes = models.PositiveIntegerField(default=0)
    flags = models.JSONField(default=list, blank=True, help_text="Things a manager may want to look at.")
    source = models.CharField(max_length=12, choices=Source.choices, default=Source.DEVICE)
    break_basis = models.CharField(
        max_length=6, blank=True, default="", choices=[("", "Hours worked"), ("rota", "Rota shift"), ("actual", "Hours worked")],
        help_text="Which length decides the break allowance when the rota and the hours worked disagree. A manager sets it.",
    )

    review_status = models.CharField(max_length=15, choices=ReviewStatus.choices, default=ReviewStatus.NOT_REQUIRED)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.CharField(max_length=300, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-clock_in_at", "-id"]
        indexes = [
            models.Index(fields=["store", "clock_in_at"], name="attendance_store_time_idx"),
            models.Index(fields=["employee", "clock_in_at"], name="attendance_emp_time_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["employee"], condition=models.Q(clock_out_at__isnull=True),
                name="one_open_attendance_record_per_employee",
            ),
            models.CheckConstraint(
                condition=models.Q(clock_out_at__isnull=True) | models.Q(clock_out_at__gt=models.F("clock_in_at")),
                name="attendance_out_after_in",
            ),
        ]

    @property
    def worked_minutes(self):
        if self.clock_out_at is None:
            return None
        return int((self.clock_out_at - self.clock_in_at).total_seconds() // 60)

    def __str__(self) -> str:
        return f"{self.employee} {self.clock_in_at:%Y-%m-%d %H:%M}"


class AttendanceEdit(models.Model):
    """Every manager change to a record, with the reason. Added to, never changed or deleted."""

    record = models.ForeignKey(AttendanceRecord, on_delete=models.PROTECT, related_name="edits")
    edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    reason = models.CharField(max_length=300)
    changes = models.JSONField(default=dict, help_text="field: [old, new]")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
