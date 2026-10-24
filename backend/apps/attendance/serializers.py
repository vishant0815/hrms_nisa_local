from rest_framework import serializers

from apps.employees.models import EmployeeProfile
from apps.employees.serializers import EmployeeBriefSerializer
from apps.stores.models import Store
from apps.stores.serializers import StoreBriefSerializer

from .models import AttendanceEdit, AttendanceRecord, ReviewStatus
from .services import OUT, base_flag

FLAG_MESSAGES = {
    "out_of_area": "Clocked away from the shop.",
    "poor_accuracy": "The phone's location was too imprecise to check.",
    "no_location": "No location was shared.",
    "shared_device": "The same device was used by someone else in the last 12 hours.",
    "late_clock_out": "Clocked out more than 4 hours after the shift ended.",
    "left_early": "Clocked out before the shift ended.",
    "manually_edited": "A manager changed the times.",
    "manual_entry": "Added by a manager.",
}


def describe_flags(flags):
    out = []
    for flag in flags:
        text = FLAG_MESSAGES.get(base_flag(flag), flag)
        out.append({
            "code": flag,
            "message": ("At clock-out: " + text[0].lower() + text[1:]) if flag.startswith(OUT) else text,
        })
    return out


class AttendanceSerializer(serializers.ModelSerializer):
    """The manager, HR and owner view. Distances are shown; raw coordinates never are."""

    employee = EmployeeBriefSerializer(read_only=True)
    store = StoreBriefSerializer(read_only=True)
    worked_minutes = serializers.IntegerField(read_only=True)
    flags = serializers.SerializerMethodField()
    reviewed_by = serializers.SerializerMethodField()

    class Meta:
        model = AttendanceRecord
        fields = [
            "id", "employee", "store", "shift", "clock_in_at", "clock_out_at", "worked_minutes",
            "late_minutes", "in_distance_m", "out_distance_m", "in_accuracy_m", "out_accuracy_m", "flags",
            "source", "review_status", "reviewed_by", "reviewed_at", "review_note", "created_at",
        ]
        read_only_fields = fields

    def get_flags(self, obj):
        return describe_flags(obj.flags)

    def get_reviewed_by(self, obj):
        return obj.reviewed_by.get_full_name() or obj.reviewed_by.email if obj.reviewed_by else None


class MyAttendanceSerializer(serializers.ModelSerializer):
    """What an employee sees about their own time. No flags, locations or other people."""

    store = StoreBriefSerializer(read_only=True)
    worked_minutes = serializers.IntegerField(read_only=True)
    status = serializers.SerializerMethodField()

    class Meta:
        model = AttendanceRecord
        fields = ["id", "store", "clock_in_at", "clock_out_at", "worked_minutes", "status", "review_note"]
        read_only_fields = fields

    def get_status(self, obj):
        if obj.clock_out_at is None:
            return "clocked_in"
        return {
            ReviewStatus.PENDING: "under_review",
            ReviewStatus.REJECTED: "rejected",
        }.get(obj.review_status, "ok")


class AttendanceEditSerializer(serializers.ModelSerializer):
    edited_by = serializers.SerializerMethodField()

    class Meta:
        model = AttendanceEdit
        fields = ["id", "edited_by", "reason", "changes", "created_at"]
        read_only_fields = fields

    def get_edited_by(self, obj):
        return obj.edited_by.get_full_name() or obj.edited_by.email if obj.edited_by else None


class ListQuerySerializer(serializers.Serializer):
    start = serializers.DateField(required=False)
    end = serializers.DateField(required=False)
    store = serializers.IntegerField(required=False)
    employee = serializers.IntegerField(required=False)
    review = serializers.ChoiceField(
        choices=[*ReviewStatus.values, "flagged", "all"], required=False, default="all"
    )


class EditSerializer(serializers.Serializer):
    clock_in_at = serializers.DateTimeField(required=False)
    clock_out_at = serializers.DateTimeField(required=False)
    reason = serializers.CharField(max_length=300, allow_blank=True, default="")


class ManualRecordSerializer(serializers.Serializer):
    employee = serializers.PrimaryKeyRelatedField(queryset=EmployeeProfile.objects.select_related("user"))
    store = serializers.PrimaryKeyRelatedField(queryset=Store.objects.all())
    shift = serializers.IntegerField(required=False, allow_null=True, default=None)
    clock_in_at = serializers.DateTimeField()
    clock_out_at = serializers.DateTimeField()
    reason = serializers.CharField(max_length=300, allow_blank=True, default="")


class DecisionSerializer(serializers.Serializer):
    note = serializers.CharField(max_length=300, allow_blank=True, default="")
