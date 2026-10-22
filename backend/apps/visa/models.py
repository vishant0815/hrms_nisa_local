"""Immigration status and right-to-work records.

Data minimisation (UK GDPR): passport numbers and share codes are deliberately NOT stored.
A right-to-work check needs the outcome, the date, who did it and a reference to where the
evidence is kept; the evidence itself stays in the employer's secure document store.
"""
import os
import uuid

from django.conf import settings
from django.core.files.storage import storages
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

EXPIRY_WARNING_DAYS = 90


class VisaType(models.Model):
    """A kind of immigration status and the work rules that go with it.

    This is configuration, not code, because Home Office rules change. Owner/HR can edit it.
    """

    name = models.CharField(max_length=100, unique=True)
    can_work = models.BooleanField(
        default=True, help_text="Does this status ever allow the holder to work?"
    )
    requires_expiry = models.BooleanField(
        default=True,
        help_text="Time-limited status. If off (citizens, settled status) any expiry date is ignored.",
    )
    max_weekly_hours = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(168)],
        help_text="Weekly cap (Monday to Sunday) outside vacation periods, e.g. 20 for Student.",
    )
    requires_sponsorship = models.BooleanField(
        default=False, help_text="Work only for the sponsoring employer; a sponsor reference is required."
    )
    requires_manual_decision = models.BooleanField(
        default=False,
        help_text="Conditions vary per person; HR must record whether this person may work.",
    )
    requires_term_letter = models.BooleanField(
        default=False,
        help_text="The person must supply a letter from their university confirming term dates "
                  "(Student). Without a verified letter a warning is raised and the weekly cap always applies.",
    )
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class VisaStatus(models.TextChoices):
    VALID = "valid", "Valid"
    EXPIRING_SOON = "expiring_soon", "Expiring soon"
    EXPIRED = "expired", "Expired"
    REVOKED = "revoked", "Revoked"
    PENDING_CHECK = "pending_check", "Right-to-work check needed"
    CHECK_FAILED = "check_failed", "Check failed"


class CheckState:
    OK = "ok"
    MISSING = "missing"
    FAILED = "failed"
    FOLLOW_UP = "follow_up"
    OVERDUE = "overdue"


class EmployeeVisa(models.Model):
    """One immigration status held by an employee. A renewal is a new row; old rows stay as history."""

    employee = models.ForeignKey("employees.EmployeeProfile", on_delete=models.PROTECT, related_name="visas")
    visa_type = models.ForeignKey(VisaType, on_delete=models.PROTECT, related_name="employee_visas")
    start_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    sponsor_reference = models.CharField(max_length=50, blank=True)
    work_conditions = models.TextField(blank=True)
    manual_work_allowed = models.BooleanField(null=True, blank=True)
    weekly_hours_cap_override = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(168)]
    )
    is_current = models.BooleanField(default=True)
    revoked_on = models.DateField(null=True, blank=True)
    revoked_reason = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_current", "-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["employee"], condition=Q(is_current=True), name="one_current_visa_per_employee"
            ),
            models.CheckConstraint(
                condition=Q(start_date__isnull=True)
                | Q(expiry_date__isnull=True)
                | Q(expiry_date__gte=F("start_date")),
                name="visa_expiry_not_before_start",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.employee} - {self.visa_type}"

    def latest_check(self):
        # `checks_desc` is set by a Prefetch in list views to avoid one query per visa.
        cached = getattr(self, "checks_desc", None)
        if cached is not None:
            return cached[0] if cached else None
        return self.checks.order_by("-checked_on", "-id").first()

    def check_state(self, on) -> str:
        check = self.latest_check()
        if check is None:
            return CheckState.MISSING
        if check.outcome == CheckOutcome.FAIL:
            return CheckState.FAILED
        if check.outcome == CheckOutcome.FOLLOW_UP:
            return CheckState.FOLLOW_UP
        if check.next_check_due and check.next_check_due < on:
            return CheckState.OVERDUE
        return CheckState.OK

    def status_on(self, on=None) -> str:
        on = on or timezone.localdate()
        if self.revoked_on:
            return VisaStatus.REVOKED
        time_limited = self.visa_type.requires_expiry and self.expiry_date is not None
        if time_limited and self.expiry_date < on:
            return VisaStatus.EXPIRED
        state = self.check_state(on)
        if state == CheckState.FAILED:
            return VisaStatus.CHECK_FAILED
        if state != CheckState.OK:
            return VisaStatus.PENDING_CHECK
        if time_limited and (self.expiry_date - on).days <= EXPIRY_WARNING_DAYS:
            return VisaStatus.EXPIRING_SOON
        return VisaStatus.VALID


def private_storage():
    """Where uploaded documents live. Not publicly served; see PRIVATE_MEDIA_ROOT in settings."""
    return storages["private_documents"]


def term_letter_path(instance, filename):
    # The stored name is random and its extension comes from the checked file type, never
    # from what the uploader called it.
    extension = os.path.splitext(filename)[1].lower()
    return f"term-letters/{instance.visa.employee_id}/{uuid.uuid4().hex}{extension}"


class LetterStatus(models.TextChoices):
    PENDING = "pending", "Waiting for HR"
    VERIFIED = "verified", "Verified"
    REJECTED = "rejected", "Rejected"


class TermLetter(models.Model):
    """A student's letter from their university confirming term and vacation dates.

    The student uploads it; it counts for nothing until HR has read it and entered the dates.
    """

    visa = models.ForeignKey(EmployeeVisa, on_delete=models.PROTECT, related_name="term_letters")
    file = models.FileField(upload_to=term_letter_path, storage=private_storage, max_length=200)
    original_filename = models.CharField(max_length=100)
    content_type = models.CharField(max_length=50)
    size_bytes = models.PositiveIntegerField()
    institution = models.CharField(max_length=150, blank=True)
    status = models.CharField(max_length=10, choices=LetterStatus.choices, default=LetterStatus.PENDING)
    covers_from = models.DateField(null=True, blank=True, help_text="Set by HR when verifying.")
    covers_to = models.DateField(null=True, blank=True, help_text="Set by HR when verifying.")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.CharField(max_length=500, blank=True, help_text="Why it was rejected.")
    suggested = models.JSONField(
        default=dict, blank=True,
        help_text="Dates the system read from the file. Only a suggestion: HR confirms the real dates.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(covers_from__isnull=True)
                | Q(covers_to__isnull=True)
                | Q(covers_to__gte=F("covers_from")),
                name="term_letter_covers_to_not_before_from",
            ),
        ]


class VacationPeriod(models.Model):
    """Official vacation dates for a student, when the weekly hours cap does not apply.

    Only periods that come from a VERIFIED term letter lift the cap.
    """

    visa = models.ForeignKey(EmployeeVisa, on_delete=models.CASCADE, related_name="vacations")
    term_letter = models.ForeignKey(
        TermLetter, null=True, blank=True, on_delete=models.PROTECT, related_name="vacations"
    )
    start_date = models.DateField()
    end_date = models.DateField()
    label = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["start_date"]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__gte=F("start_date")), name="vacation_end_not_before_start"
            ),
        ]


class CheckType(models.TextChoices):
    ONLINE = "online", "Home Office online check (share code)"
    MANUAL = "manual", "Manual document check"
    IDSP = "idsp", "Identity service provider (British/Irish passport)"


class CheckOutcome(models.TextChoices):
    PASS = "pass", "Pass"
    FAIL = "fail", "Fail"
    FOLLOW_UP = "follow_up", "Follow-up needed"


class RightToWorkCheck(models.Model):
    """An audit-trail entry. Never edited or deleted: a correction is a new check."""

    employee = models.ForeignKey("employees.EmployeeProfile", on_delete=models.PROTECT, related_name="rtw_checks")
    visa = models.ForeignKey(EmployeeVisa, on_delete=models.PROTECT, related_name="checks")
    check_type = models.CharField(max_length=10, choices=CheckType.choices)
    outcome = models.CharField(max_length=10, choices=CheckOutcome.choices)
    checked_on = models.DateField()
    next_check_due = models.DateField(null=True, blank=True)
    evidence_reference = models.CharField(
        max_length=200, blank=True, help_text="Where the evidence is kept. Not the document itself."
    )
    notes = models.TextField(blank=True)
    checked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-checked_on", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(next_check_due__isnull=True) | Q(next_check_due__gt=F("checked_on")),
                name="rtw_next_check_after_check",
            ),
        ]


class VisaAuditLog(models.Model):
    """Who did what to whose immigration record. Holds no document values, only what happened."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=40)
    employee = models.ForeignKey("employees.EmployeeProfile", on_delete=models.PROTECT, related_name="+")
    visa = models.ForeignKey(EmployeeVisa, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    detail = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
