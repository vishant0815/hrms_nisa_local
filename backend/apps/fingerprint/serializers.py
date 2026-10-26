from rest_framework import serializers

from apps.employees.models import EmployeeProfile
from apps.employees.serializers import EmployeeBriefSerializer
from apps.stores.models import Store
from apps.stores.serializers import StoreBriefSerializer

from .models import ClockDevice, Enrolment, Punch

REASONS = {
    "unknown_user": "This fingerprint number is not enrolled.",
    "withdrawn": "This person has withdrawn their fingerprint consent.",
    "no_shift": "Not rostered to work at that time.",
    "too_early": "Punched in more than 30 minutes before the shift.",
    "already_worked": "A record already exists for this shift.",
    "already_clocked_in": "Already clocked in.",
    "not_clocked_in": "Not clocked in.",
    "already_on_break": "Already on a break.",
    "not_on_break": "Not on a break.",
    "bad_order": "The time is before the thing it follows.",
    "too_soon": "Clocked out within a minute of clocking in.",
    "not_employed": "Not employed on that date.",
    "device_clock_wrong": "The device's clock is wrong (in the future, or over a day old).",
    "double_tap": "A repeat of the same punch within a minute.",
    "confirms_app_clock_in": "Already clocked in on the app; the device confirms it.",
}

SERIAL = r"^[A-Za-z0-9_.:-]{3,64}$"


class ClockDeviceSerializer(serializers.ModelSerializer):
    store = StoreBriefSerializer(read_only=True)
    online = serializers.BooleanField(read_only=True)

    class Meta:
        model = ClockDevice
        fields = ["id", "store", "name", "serial", "is_active", "online", "last_seen_at", "clock_skew_seconds", "created_at"]
        read_only_fields = fields


class DeviceCreateSerializer(serializers.Serializer):
    store = serializers.PrimaryKeyRelatedField(queryset=Store.objects.all())
    name = serializers.CharField(max_length=100)
    serial = serializers.RegexField(SERIAL)


class DeviceUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100, required=False)
    is_active = serializers.BooleanField(required=False)


class EnrolmentSerializer(serializers.ModelSerializer):
    employee = EmployeeBriefSerializer(read_only=True)
    active = serializers.BooleanField(read_only=True)

    class Meta:
        model = Enrolment
        fields = [
            "id", "employee", "device_user_id", "active", "consent_note", "consented_at", "withdrawn_at", "withdrawn_reason",
        ]
        read_only_fields = fields


class EnrolSerializer(serializers.Serializer):
    employee = serializers.PrimaryKeyRelatedField(queryset=EmployeeProfile.objects.select_related("user"))
    consent_note = serializers.CharField(max_length=300, allow_blank=True, default="")


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=300, allow_blank=True, default="")


class PunchSerializer(serializers.ModelSerializer):
    device = serializers.SerializerMethodField()
    employee = EmployeeBriefSerializer(read_only=True)
    message = serializers.SerializerMethodField()

    class Meta:
        model = Punch
        fields = [
            "id", "device", "device_user_id", "kind", "device_time", "received_at", "effective_at", "employee",
            "record", "outcome", "reason", "message",
        ]
        read_only_fields = fields

    def get_device(self, obj):
        return {"id": obj.device_id, "name": obj.device.name, "store": obj.device.store.name}

    def get_message(self, obj):
        return REASONS.get(obj.reason, "")


class PunchQuerySerializer(serializers.Serializer):
    start = serializers.DateField(required=False)
    end = serializers.DateField(required=False)
    device = serializers.IntegerField(required=False)
    employee = serializers.IntegerField(required=False)
    outcome = serializers.ChoiceField(choices=["accepted", "duplicate", "rejected", "all"], required=False, default="all")


class BreakReportQuerySerializer(serializers.Serializer):
    start = serializers.DateField(required=False)
    end = serializers.DateField(required=False)
    store = serializers.IntegerField(required=False)
    employee = serializers.IntegerField(required=False)


class BreakBasisSerializer(serializers.Serializer):
    basis = serializers.ChoiceField(choices=["rota", "actual"])
    reason = serializers.CharField(max_length=300, allow_blank=True, default="")
