from decimal import Decimal

from rest_framework import serializers

from apps.employees.serializers import EmployeeBriefSerializer

from .models import EmployeeVisa, RightToWorkCheck, VacationPeriod, VisaType


class VisaTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = VisaType
        fields = [
            "id", "name", "can_work", "requires_expiry", "max_weekly_hours",
            "requires_sponsorship", "requires_manual_decision", "notes", "is_active",
        ]
        read_only_fields = ["id"]


class VisaTypeBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = VisaType
        fields = ["id", "name"]
        read_only_fields = fields


class LatestCheckSerializer(serializers.ModelSerializer):
    class Meta:
        model = RightToWorkCheck
        fields = ["id", "outcome", "checked_on", "next_check_due"]
        read_only_fields = fields


class EmployeeVisaSerializer(serializers.ModelSerializer):
    employee = EmployeeBriefSerializer(read_only=True)
    visa_type = VisaTypeBriefSerializer(read_only=True)
    status = serializers.SerializerMethodField()
    latest_check = serializers.SerializerMethodField()

    class Meta:
        model = EmployeeVisa
        fields = [
            "id", "employee", "visa_type", "start_date", "expiry_date", "sponsor_reference",
            "work_conditions", "manual_work_allowed", "weekly_hours_cap_override",
            "is_current", "revoked_on", "revoked_reason", "status", "latest_check", "created_at",
        ]
        read_only_fields = fields

    def get_status(self, obj):
        return obj.status_on()

    def get_latest_check(self, obj):
        check = obj.latest_check()
        return LatestCheckSerializer(check).data if check else None


class EmployeeVisaCreateSerializer(serializers.Serializer):
    visa_type = serializers.PrimaryKeyRelatedField(queryset=VisaType.objects.all())
    start_date = serializers.DateField(required=False, allow_null=True)
    expiry_date = serializers.DateField(required=False, allow_null=True)
    sponsor_reference = serializers.CharField(max_length=50, required=False, allow_blank=True, default="")
    work_conditions = serializers.CharField(required=False, allow_blank=True, default="")
    manual_work_allowed = serializers.BooleanField(required=False, allow_null=True, default=None)
    weekly_hours_cap_override = serializers.IntegerField(
        required=False, allow_null=True, min_value=1, max_value=168
    )


class EmployeeVisaUpdateSerializer(serializers.Serializer):
    """Only details can be corrected. To change the visa type, add a new status."""

    start_date = serializers.DateField(required=False, allow_null=True)
    expiry_date = serializers.DateField(required=False, allow_null=True)
    sponsor_reference = serializers.CharField(max_length=50, required=False, allow_blank=True)
    work_conditions = serializers.CharField(required=False, allow_blank=True)
    manual_work_allowed = serializers.BooleanField(required=False, allow_null=True)
    weekly_hours_cap_override = serializers.IntegerField(
        required=False, allow_null=True, min_value=1, max_value=168
    )


class RevokeSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")


class VacationPeriodSerializer(serializers.ModelSerializer):
    class Meta:
        model = VacationPeriod
        fields = ["id", "start_date", "end_date", "label"]
        read_only_fields = ["id"]

    def validate(self, attrs):
        if attrs["end_date"] < attrs["start_date"]:
            raise serializers.ValidationError({"end_date": "The end date cannot be before the start date."})
        return attrs


class RightToWorkCheckSerializer(serializers.ModelSerializer):
    checked_on = serializers.DateField(required=False)  # defaults to today in the view

    class Meta:
        model = RightToWorkCheck
        fields = [
            "id", "visa", "check_type", "outcome", "checked_on", "next_check_due",
            "evidence_reference", "notes", "checked_by", "created_at",
        ]
        read_only_fields = ["id", "checked_by", "created_at"]


class CanWorkQuerySerializer(serializers.Serializer):
    date = serializers.DateField(required=False)
    week_hours = serializers.DecimalField(
        required=False, max_digits=5, decimal_places=2, min_value=Decimal("0"), max_value=Decimal("168")
    )
