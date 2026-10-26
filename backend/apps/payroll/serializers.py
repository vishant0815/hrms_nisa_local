from rest_framework import serializers

from . import rules
from . import crypto
from .models import PayRate, PayRun, Payslip, PayslipTemplate


class PayslipTemplateSerializer(serializers.ModelSerializer):
    has_logo = serializers.SerializerMethodField()

    class Meta:
        model = PayslipTemplate
        fields = [
            "trading_name", "legal_employer_name", "employer_address", "paye_reference", "company_number",
            "hr_email", "footer_note", "accent_colour", "show_bank_line", "has_logo", "updated_at",
        ]
        read_only_fields = ["has_logo", "updated_at"]

    def get_has_logo(self, template):
        return bool(template.logo)

    def validate_trading_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("The trading name cannot be empty.")
        return value

    def validate_accent_colour(self, value):
        problem = rules.accent_problem(value)
        if problem:
            raise serializers.ValidationError(problem)
        return value.lower()


class RateSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayRate
        fields = ["id", "hourly_rate", "effective_from", "created_at"]
        read_only_fields = ["id", "created_at"]


class PayDetailsInput(serializers.Serializer):
    ni_number = serializers.CharField(max_length=20, required=False, allow_blank=True)
    tax_code = serializers.CharField(max_length=12, required=False, allow_blank=True)
    account_name = serializers.CharField(max_length=60, required=False, allow_blank=True)
    sort_code = serializers.CharField(max_length=12, required=False, allow_blank=True)
    account_number = serializers.CharField(max_length=14, required=False, allow_blank=True)


def mask_details(details):
    """What the API shows: enough to recognise the account, never the whole number."""
    sort_code = crypto.decrypt(details.sort_code_encrypted) if details else ""
    account = crypto.decrypt(details.account_number_encrypted) if details else ""
    ni = details.ni_number if details else ""
    return {
        "ni_number": f"{ni[:2]}******{ni[-1:]}" if ni else "",
        "tax_code": details.tax_code if details else "",
        "account_name": details.account_name if details else "",
        "sort_code": f"**-**-{sort_code[-2:]}" if sort_code else "",
        "account_number": f"****{account[-4:]}" if account else "",
        "has_bank_details": bool(sort_code and account),
    }


class PayslipSerializer(serializers.ModelSerializer):
    employee_number = serializers.CharField(source="employee.employee_number")
    employee_name = serializers.SerializerMethodField()
    total_deductions = serializers.DecimalField(max_digits=9, decimal_places=2, read_only=True)
    net_pay = serializers.DecimalField(max_digits=9, decimal_places=2, read_only=True)

    class Meta:
        model = Payslip
        fields = [
            "id", "number", "employee", "employee_number", "employee_name", "store", "paid_minutes",
            "unpaid_break_minutes", "gross_pay", "earnings", "days", "held", "income_tax", "national_insurance",
            "pension", "student_loan", "other_deductions", "total_deductions", "net_pay", "deductions_confirmed",
            "emailed_at",
        ]

    def get_employee_name(self, slip):
        return slip.employee.user.get_full_name() or slip.employee.employee_number


class DeductionsInput(serializers.Serializer):
    income_tax = serializers.DecimalField(max_digits=9, decimal_places=2, required=False)
    national_insurance = serializers.DecimalField(max_digits=9, decimal_places=2, required=False)
    pension = serializers.DecimalField(max_digits=9, decimal_places=2, required=False)
    student_loan = serializers.DecimalField(max_digits=9, decimal_places=2, required=False)
    other_deductions = serializers.DecimalField(max_digits=9, decimal_places=2, required=False)
    confirmed = serializers.BooleanField(required=False)


class PayRunSerializer(serializers.ModelSerializer):
    payslip_count = serializers.SerializerMethodField()
    total_gross = serializers.SerializerMethodField()

    class Meta:
        model = PayRun
        fields = ["id", "period_start", "period_end", "pay_date", "status", "approved_at", "payslip_count", "total_gross"]

    def get_payslip_count(self, run):
        return run.payslips.count()

    def get_total_gross(self, run):
        return str(sum((p.gross_pay for p in run.payslips.all()), 0))


class CreateRunInput(serializers.Serializer):
    period_start = serializers.DateField()
    pay_date = serializers.DateField(required=False)
