from django.conf import settings
from django.db import models


class ShiftTemplate(models.Model):
    """A reusable shift pattern for one store (e.g. Opening 07:00-15:00). A shortcut, not a rule."""

    store = models.ForeignKey("stores.Store", on_delete=models.PROTECT, related_name="shift_templates")
    name = models.CharField(max_length=50)
    start_time = models.TimeField()
    end_time = models.TimeField()
    break_minutes = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["store", "start_time", "name"]
        constraints = [
            models.UniqueConstraint(fields=["store", "name"], name="unique_template_name_per_store"),
        ]

    def __str__(self) -> str:
        return f"{self.store} - {self.name}"


class WeekStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PUBLISHED = "published", "Published"


class RotaWeek(models.Model):
    """One store's rota for one Monday-to-Sunday week. Employees see it only once published."""

    store = models.ForeignKey("stores.Store", on_delete=models.PROTECT, related_name="rota_weeks")
    week_start = models.DateField(help_text="Always a Monday.")
    status = models.CharField(max_length=10, choices=WeekStatus.choices, default=WeekStatus.DRAFT)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    published_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-week_start", "store"]
        constraints = [
            models.UniqueConstraint(fields=["store", "week_start"], name="one_rota_week_per_store"),
        ]

    def __str__(self) -> str:
        return f"{self.store} week of {self.week_start}"


class ShiftStatus(models.TextChoices):
    SCHEDULED = "scheduled", "Scheduled"
    CANCELLED = "cancelled", "Cancelled"


class Shift(models.Model):
    """A block of work on one date. No employee means an open shift. Cancelled, never deleted.

    If `end_time` is not after `start_time` the shift runs past midnight into the next day.
    """

    rota_week = models.ForeignKey(RotaWeek, on_delete=models.PROTECT, related_name="shifts")
    employee = models.ForeignKey(
        "employees.EmployeeProfile", null=True, blank=True, on_delete=models.PROTECT, related_name="shifts"
    )
    date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    break_minutes = models.PositiveSmallIntegerField(default=0)
    role = models.CharField(max_length=50, blank=True, help_text="e.g. Cashier, Stock, Supervisor")
    status = models.CharField(max_length=10, choices=ShiftStatus.choices, default=ShiftStatus.SCHEDULED)
    notes = models.TextField(blank=True, help_text="Private to managers, HR and the owner.")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["date", "start_time", "id"]
        indexes = [models.Index(fields=["employee", "date"], name="shift_employee_date_idx")]

    def __str__(self) -> str:
        return f"{self.rota_week.store} {self.date} {self.start_time:%H:%M}-{self.end_time:%H:%M}"


class SwapStatus(models.TextChoices):
    PENDING_COLLEAGUE = "pending_colleague", "Waiting for the colleague"
    PENDING_MANAGER = "pending_manager", "Waiting for the manager"
    APPROVED = "approved", "Approved"
    DECLINED = "declined", "Declined by the colleague"
    REJECTED = "rejected", "Rejected by the manager"
    CANCELLED = "cancelled", "Cancelled by the requester"


OPEN_SWAP_STATUSES = (SwapStatus.PENDING_COLLEAGUE, SwapStatus.PENDING_MANAGER)


class ShiftSwap(models.Model):
    """One employee hands a shift to a colleague, who may give one of their own back.

    Nothing changes on the rota until a manager approves. Then both shifts change hands in one
    step, after every rota rule has been checked again.
    """

    offered_shift = models.ForeignKey(Shift, on_delete=models.PROTECT, related_name="swaps_offered")
    requested_shift = models.ForeignKey(
        Shift, null=True, blank=True, on_delete=models.PROTECT, related_name="swaps_requested",
        help_text="The colleague's shift given back in return. Empty means a plain hand-over.",
    )
    requester = models.ForeignKey("employees.EmployeeProfile", on_delete=models.PROTECT, related_name="swap_requests")
    colleague = models.ForeignKey("employees.EmployeeProfile", on_delete=models.PROTECT, related_name="swap_invitations")
    status = models.CharField(max_length=20, choices=SwapStatus.choices, default=SwapStatus.PENDING_COLLEAGUE)
    note = models.CharField(max_length=300, blank=True)
    decision_note = models.CharField(max_length=300, blank=True, help_text="The manager's reason, if rejected.")
    colleague_responded_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["offered_shift"],
                condition=models.Q(status__in=["pending_colleague", "pending_manager"]),
                name="one_open_swap_per_offered_shift",
            ),
            models.CheckConstraint(
                condition=~models.Q(requester=models.F("colleague")), name="swap_with_someone_else",
            ),
        ]

    def __str__(self) -> str:
        return f"Swap {self.pk} ({self.status})"
