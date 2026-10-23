from rest_framework import serializers

from apps.stores.serializers import StoreBriefSerializer

from .models import OPEN_SWAP_STATUSES, Shift, ShiftSwap, SwapStatus
from .services import shift_hours
from .swaps import person_name, rule_problems


class SwapShiftSerializer(serializers.ModelSerializer):
    """Just the facts about a shift that a swap needs. Never the manager's private notes."""

    store = StoreBriefSerializer(source="rota_week.store", read_only=True)
    hours = serializers.SerializerMethodField()

    class Meta:
        model = Shift
        fields = ["id", "store", "date", "start_time", "end_time", "break_minutes", "hours", "role"]
        read_only_fields = fields

    def get_hours(self, obj):
        return str(shift_hours(obj.date, obj.start_time, obj.end_time, obj.break_minutes))


class PersonSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.SerializerMethodField()

    def get_name(self, obj):
        return person_name(obj)  # never the email address


def kind_of(swap):
    return "swap" if swap.requested_shift_id else "hand_over"


class ManagerSwapSerializer(serializers.ModelSerializer):
    """For managers, HR and the owner. A waiting swap says what would stop it if approved now."""

    store = StoreBriefSerializer(source="offered_shift.rota_week.store", read_only=True)
    requester = PersonSerializer(read_only=True)
    colleague = PersonSerializer(read_only=True)
    offered_shift = SwapShiftSerializer(read_only=True)
    requested_shift = SwapShiftSerializer(read_only=True)
    kind = serializers.SerializerMethodField()
    problems_if_approved = serializers.SerializerMethodField()

    class Meta:
        model = ShiftSwap
        fields = [
            "id", "kind", "status", "store", "requester", "colleague", "offered_shift", "requested_shift",
            "note", "decision_note", "problems_if_approved", "colleague_responded_at", "reviewed_at", "created_at",
        ]
        read_only_fields = fields

    def get_kind(self, obj):
        return kind_of(obj)

    def get_problems_if_approved(self, obj):
        if obj.status != SwapStatus.PENDING_MANAGER:
            return []
        return rule_problems(obj.offered_shift, obj.requested_shift, obj.requester, obj.colleague)


class MySwapSerializer(serializers.ModelSerializer):
    """For the two employees involved."""

    kind = serializers.SerializerMethodField()
    direction = serializers.SerializerMethodField()
    other_person = serializers.SerializerMethodField()
    offered_shift = SwapShiftSerializer(read_only=True)
    requested_shift = SwapShiftSerializer(read_only=True)

    class Meta:
        model = ShiftSwap
        fields = [
            "id", "kind", "direction", "status", "other_person", "offered_shift", "requested_shift",
            "note", "decision_note", "created_at",
        ]
        read_only_fields = fields

    def get_kind(self, obj):
        return kind_of(obj)

    def get_direction(self, obj):
        return "outgoing" if obj.requester_id == self.context["me"].pk else "incoming"

    def get_other_person(self, obj):
        other = obj.colleague if obj.requester_id == self.context["me"].pk else obj.requester
        return PersonSerializer(other).data


class SwapRequestSerializer(serializers.Serializer):
    offered_shift = serializers.IntegerField()
    colleague = serializers.IntegerField()
    note = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


class SwapAcceptSerializer(serializers.Serializer):
    in_return_shift = serializers.IntegerField(required=False, allow_null=True, default=None)


class SwapRejectSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")
