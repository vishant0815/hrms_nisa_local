from rest_framework import serializers

from . import rules
from .models import PayslipTemplate


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
