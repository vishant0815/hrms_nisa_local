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
