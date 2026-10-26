from django.conf import settings
from django.core.files.storage import storages
from django.db import models
from django.db.models import F, Q


def private_storage():
    return storages["private_documents"]


def logo_path(instance, filename):
    return f"payslip-template/logo{filename[filename.rfind('.'):].lower()}"


class PayslipTemplate(models.Model):
    """The one set of wording, colours and logo that every payslip uses. Always the row with pk=1."""

    trading_name = models.CharField(max_length=80, default="Nisa Local")
    legal_employer_name = models.CharField(max_length=120, blank=True)
    employer_address = models.TextField(max_length=300, blank=True)
    paye_reference = models.CharField(max_length=30, blank=True)
    company_number = models.CharField(max_length=30, blank=True)
    hr_email = models.EmailField(blank=True)
    footer_note = models.TextField(
        max_length=400, blank=True,
        default="Please keep this payslip: it is a record of your pay and tax.",
    )
    accent_colour = models.CharField(max_length=7, default="#1d5c3a")
    show_bank_line = models.BooleanField(default=True)
    logo = models.FileField(upload_to=logo_path, storage=private_storage, max_length=200, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def current(cls):
        template, _ = cls.objects.get_or_create(pk=1)
        return template

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise models.ProtectedError("The payslip template cannot be deleted; edit it instead.", {self})


class PayRate(models.Model):
    """An hourly rate from a date onwards. Rates are added, never changed, so past pay can be explained."""

    employee = models.ForeignKey("employees.EmployeeProfile", on_delete=models.PROTECT, related_name="pay_rates")
    hourly_rate = models.DecimalField(max_digits=7, decimal_places=2)
    effective_from = models.DateField()
    set_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["employee", "-effective_from"]
        constraints = [
            models.UniqueConstraint(fields=["employee", "effective_from"], name="one_rate_per_employee_per_day"),
            models.CheckConstraint(condition=Q(hourly_rate__gt=0), name="pay_rate_positive"),
        ]


class PayDetails(models.Model):
    """What a payslip and a bank payment need to know about a person. Bank numbers are stored encrypted."""

    employee = models.OneToOneField("employees.EmployeeProfile", on_delete=models.PROTECT, related_name="pay_details")
    ni_number = models.CharField(max_length=13, blank=True)
    tax_code = models.CharField(max_length=10, blank=True)
    account_name = models.CharField(max_length=60, blank=True)
    date_of_birth_encrypted = models.TextField(blank=True)
    typical_day_minutes = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="Used for holiday pay until there is enough pay history to average (a new starter, or the first weeks).",
    )
    ni_category = models.CharField(
        max_length=1, blank=True, help_text="Leave blank for the usual letter (A, or M under 21). Set C after State Pension age.",
    )
    student_loan_plan = models.CharField(max_length=1, blank=True, choices=[("1", "Plan 1"), ("2", "Plan 2"), ("4", "Plan 4"), ("5", "Plan 5")])
    postgrad_loan = models.BooleanField(default=False)
    pension_opted_out = models.BooleanField(default=False)
    apprentice_from = models.DateField(null=True, blank=True, help_text="Start of an apprenticeship, for the apprentice minimum wage.")
    prior_pay = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Taxable pay this tax year before the first payslip here (from the P45). Needed mid-year for a cumulative tax code.",
    )
    prior_tax = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text="Tax paid on that pay.")
    sort_code_encrypted = models.TextField(blank=True)
    account_number_encrypted = models.TextField(blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)


class HoursApproval(models.Model):
    """A manager's OK for clocked time that runs longer than the rota, so that it is paid."""

    record = models.OneToOneField("attendance.AttendanceRecord", on_delete=models.PROTECT, related_name="hours_approval")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    note = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class RunStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    APPROVED = "approved", "Approved"
    SENT = "sent", "Sent"


class PayRun(models.Model):
    """One pay week. Built as a draft, checked, approved by Owner or HR, then sent on the pay date."""

    period_start = models.DateField(unique=True)
    period_end = models.DateField()
    pay_date = models.DateField()
    status = models.CharField(max_length=10, choices=RunStatus.choices, default=RunStatus.DRAFT)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    template_snapshot = models.JSONField(default=dict, blank=True, help_text="The template wording when it was approved.")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-period_start"]
        constraints = [models.CheckConstraint(condition=Q(period_end__gte=F("period_start")), name="pay_run_end_after_start")]


class Payslip(models.Model):
    """One person's pay for a run. The hours and money are a snapshot: later edits do not change them."""

    pay_run = models.ForeignKey(PayRun, on_delete=models.CASCADE, related_name="payslips")
    employee = models.ForeignKey("employees.EmployeeProfile", on_delete=models.PROTECT, related_name="payslips")
    number = models.CharField(max_length=40)
    store = models.ForeignKey("stores.Store", null=True, on_delete=models.PROTECT, related_name="+")
    paid_minutes = models.PositiveIntegerField(default=0)
    unpaid_break_minutes = models.PositiveIntegerField(default=0)
    gross_pay = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    days = models.JSONField(default=list, help_text="One row per attendance record that is paid on this payslip.")
    held = models.JSONField(default=list, help_text="Records not paid yet, and why.")
    record_ids = models.JSONField(default=list)
    leave_dates = models.JSONField(default=dict, help_text="Annual leave paid on this payslip: {request id: [dates]}.")
    sick_dates = models.JSONField(default=dict, help_text="Sick days covered by this payslip: {request id: [dates]}.")
    sick_pay = models.DecimalField(max_digits=9, decimal_places=2, default=0, help_text="Statutory sick pay, from the accountant.")
    earnings = models.JSONField(default=list, help_text="Pay lines: label, minutes, rate, amount.")

    income_tax = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    national_insurance = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    pension = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    student_loan = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    other_deductions = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    deduction_notes = models.JSONField(default=list, blank=True, help_text="Why the suggested deductions are what they are, and what could not be worked out.")
    deductions_confirmed = models.BooleanField(default=False)
    deductions_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    emailed_at = models.DateTimeField(null=True, blank=True)
    pdf = models.FileField(upload_to="payslips/%Y/", storage=private_storage, max_length=200, blank=True)

    class Meta:
        ordering = ["employee__employee_number"]
        constraints = [
            models.UniqueConstraint(fields=["pay_run", "employee"], name="one_payslip_per_person_per_run"),
            models.CheckConstraint(condition=Q(sick_pay__gte=0), name="payslip_sick_pay_not_negative"),
            models.CheckConstraint(
                condition=Q(income_tax__gte=0, national_insurance__gte=0, pension__gte=0, student_loan__gte=0, other_deductions__gte=0),
                name="payslip_deductions_not_negative",
            ),
        ]

    @property
    def total_deductions(self):
        return self.income_tax + self.national_insurance + self.pension + self.student_loan + self.other_deductions

    @property
    def net_pay(self):
        return self.gross_pay - self.total_deductions
