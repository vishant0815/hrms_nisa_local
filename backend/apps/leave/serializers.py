from rest_framework import serializers

from apps.employees.serializers import EmployeeBriefSerializer

from .models import HalfDay, LeaveKind, LeaveRequest


class LeaveCreateSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=LeaveKind.choices)
    start_date = serializers.DateField()
    end_date = serializers.DateField(required=False, allow_null=True)
    half_day = serializers.ChoiceField(choices=HalfDay.choices, required=False, allow_blank=True, default="")
    note = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


class MyLeaveSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeaveRequest
        fields = [
            "id", "kind", "start_date", "end_date", "half_day", "days_requested", "days_charged",
            "status", "note", "decision_note", "created_at",
        ]
        read_only_fields = fields


class ManagerLeaveSerializer(serializers.ModelSerializer):
    employee = EmployeeBriefSerializer(read_only=True)
    reviewed_by = serializers.SerializerMethodField()
    annual_leave_left = serializers.SerializerMethodField()

    class Meta:
        model = LeaveRequest
        fields = [
            "id", "employee", "kind", "start_date", "end_date", "half_day", "days_requested", "days_charged",
            "status", "note", "decision_note", "reviewed_by", "reviewed_at", "annual_leave_left", "created_at",
        ]
        read_only_fields = fields

    def get_reviewed_by(self, obj):
        return (obj.reviewed_by.get_full_name() or obj.reviewed_by.email) if obj.reviewed_by else None

    def get_annual_leave_left(self, obj):
        """What is left to decide with, for annual leave still waiting."""
        from . import services

        if obj.kind != "annual" or obj.status != "pending":
            return None
        info = services.balance(obj.employee, obj.start_date)
        return info["allowance"] - info["used"]


class CalendarEntrySerializer(serializers.ModelSerializer):
    """Who is off and when. The kind of leave is deliberately left out."""

    employee = EmployeeBriefSerializer(read_only=True)

    class Meta:
        model = LeaveRequest
        fields = ["id", "employee", "start_date", "end_date", "half_day"]
        read_only_fields = fields


class ApproveSerializer(serializers.Serializer):
    days_charged = serializers.DecimalField(max_digits=5, decimal_places=1, required=False, allow_null=True)


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=300, allow_blank=True, default="")


class AllowanceSerializer(serializers.Serializer):
    days = serializers.DecimalField(max_digits=5, decimal_places=1, min_value=0)
    date = serializers.DateField(required=False, help_text="Any date in the leave year. Defaults to today.")


class DateQuerySerializer(serializers.Serializer):
    date = serializers.DateField(required=False)


class RangeQuerySerializer(serializers.Serializer):
    start = serializers.DateField()
    end = serializers.DateField()
