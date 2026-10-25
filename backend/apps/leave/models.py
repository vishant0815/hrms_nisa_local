from django.conf import settings
from django.db import models
from django.db.models import F, Q


class LeaveKind(models.TextChoices):
    ANNUAL = "annual", "Annual leave"
    UNPAID = "unpaid", "Unpaid leave"
    SICK = "sick", "Sick leave"
    OTHER = "other", "Other (for example family or compassionate)"


class LeaveStatus(models.TextChoices):
    PENDING = "pending", "Waiting for a decision"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    CANCELLED = "cancelled", "Cancelled"


class HalfDay(models.TextChoices):
    NONE = "", "Whole day(s)"
    MORNING = "am", "Morning"
    AFTERNOON = "pm", "Afternoon"


# Statuses that hold the dates (and, for annual leave, the allowance).
LIVE_STATUSES = (LeaveStatus.PENDING, LeaveStatus.APPROVED)


class LeaveAllowance(models.Model):
    """Annual-leave days HR has set for one person for one leave year.

    Without a row the default applies (settings.DEFAULT_ANNUAL_LEAVE_DAYS, shared out by the days
    the person is employed in that year).
    """

    employee = models.ForeignKey("employees.EmployeeProfile", on_delete=models.PROTECT, related_name="leave_allowances")
    year_start = models.DateField(help_text="First day of the leave year this applies to.")
    days = models.DecimalField(max_digits=5, decimal_places=1)
    set_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["employee", "year_start"], name="one_allowance_per_leave_year"),
            models.CheckConstraint(condition=Q(days__gte=0), name="allowance_not_negative"),
        ]

    def __str__(self) -> str:
        return f"{self.employee_id}: {self.days} days from {self.year_start}"


class LeaveRequest(models.Model):
    """A request for time off, or (for sickness) a report of it.

    Annual, unpaid and other leave wait for a manager. Sickness is recorded straight away and the
    managers are told, because it cannot be planned or refused.
    """

    employee = models.ForeignKey("employees.EmployeeProfile", on_delete=models.PROTECT, related_name="leave_requests")
    kind = models.CharField(max_length=10, choices=LeaveKind.choices)
    start_date = models.DateField()
    end_date = models.DateField()
    half_day = models.CharField(max_length=2, choices=HalfDay.choices, blank=True, default="")
    days_requested = models.DecimalField(max_digits=5, decimal_places=1)
    days_charged = models.DecimalField(
        max_digits=5, decimal_places=1, null=True, blank=True,
        help_text="Annual leave only: the days taken off the allowance once approved.",
    )
    status = models.CharField(max_length=10, choices=LeaveStatus.choices, default=LeaveStatus.PENDING)
    note = models.CharField(max_length=300, blank=True)
    decision_note = models.CharField(max_length=300, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["start_date", "id"]
        indexes = [models.Index(fields=["employee", "start_date", "end_date"], name="leave_employee_dates_idx")]
        constraints = [
            models.CheckConstraint(condition=Q(end_date__gte=F("start_date")), name="leave_end_not_before_start"),
            models.CheckConstraint(condition=Q(days_requested__gt=0), name="leave_days_positive"),
        ]

    def __str__(self) -> str:
        return f"Leave {self.pk} ({self.kind}, {self.status})"
