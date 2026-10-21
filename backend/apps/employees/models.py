from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from apps.stores.models import Store


class EmployeeProfile(models.Model):
    """Work details for a user who works in the shops (a manager or an employee).

    Deliberately minimal: sensitive records such as visa details live in their own app
    with stricter access.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="employee_profile"
    )
    employee_number = models.CharField(max_length=20, unique=True, null=True, blank=True, editable=False)
    phone = models.CharField(max_length=30, blank=True)
    job_title = models.CharField(max_length=100, blank=True)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["employee_number"]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__isnull=True) | Q(end_date__gte=F("start_date")),
                name="employee_end_not_before_start",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.employee_number or 'new'} {self.user}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.employee_number:
            self.employee_number = f"EMP-{self.pk:04d}"
            super().save(update_fields=["employee_number"])

    def is_current(self, on=None) -> bool:
        on = on or timezone.localdate()
        return self.end_date is None or self.end_date >= on


class AssignmentKind(models.TextChoices):
    HOME = "home", "Home store"
    COVER = "cover", "Cover at another store"


class AssignmentStatus(models.TextChoices):
    PENDING = "pending", "Pending approval"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    CANCELLED = "cancelled", "Cancelled"


class StoreAssignment(models.Model):
    """Which store an employee works at, and for which dates.

    - HOME: the employee's own shop. Created approved, open-ended until a transfer.
    - COVER: working at another shop for a period. Needs approval from the manager of the
      home store AND the manager of the destination store before it becomes approved.
    """

    employee = models.ForeignKey(EmployeeProfile, on_delete=models.PROTECT, related_name="assignments")
    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name="assignments")
    kind = models.CharField(max_length=10, choices=AssignmentKind.choices)
    status = models.CharField(
        max_length=10, choices=AssignmentStatus.choices, default=AssignmentStatus.PENDING
    )
    valid_from = models.DateField()
    valid_to = models.DateField(null=True, blank=True)  # null = open-ended (home stores)
    reason = models.TextField(blank=True)

    # Cover requests only: the employee's home store when the request was made.
    home_store = models.ForeignKey(Store, null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    home_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    home_approved_at = models.DateTimeField(null=True, blank=True)
    destination_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    destination_approved_at = models.DateTimeField(null=True, blank=True)
    rejected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    rejection_reason = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-valid_from", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(valid_to__isnull=True) | Q(valid_to__gte=F("valid_from")),
                name="assignment_end_not_before_start",
            ),
            # An employee has at most one open-ended approved home store at a time.
            models.UniqueConstraint(
                fields=["employee"],
                condition=Q(kind="home", status="approved", valid_to__isnull=True),
                name="one_open_home_store_per_employee",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.employee} @ {self.store} ({self.kind}, {self.status})"
