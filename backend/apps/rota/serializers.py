from datetime import timedelta

from rest_framework import serializers

from apps.employees.scoping import employees_visible_to
from apps.employees.serializers import EmployeeBriefSerializer
from apps.stores.serializers import StoreBriefSerializer

from .models import RotaWeek, Shift, ShiftTemplate
from .services import shift_hours


class ShiftTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShiftTemplate
        fields = ["id", "store", "name", "start_time", "end_time", "break_minutes", "is_active"]
        read_only_fields = ["id", "store"]

    def validate(self, attrs):
        start = attrs.get("start_time", getattr(self.instance, "start_time", None))
        end = attrs.get("end_time", getattr(self.instance, "end_time", None))
        if start is not None and start == end:
            raise serializers.ValidationError("The start and end times cannot be the same.")
        return attrs


class ShiftTemplateCreateSerializer(ShiftTemplateSerializer):
    store = serializers.IntegerField()

    class Meta(ShiftTemplateSerializer.Meta):
        read_only_fields = ["id"]


class ShiftSerializer(serializers.ModelSerializer):
    """The manager/HR/owner view of a shift."""

    store = StoreBriefSerializer(source="rota_week.store", read_only=True)
    employee = EmployeeBriefSerializer(read_only=True)
    hours = serializers.SerializerMethodField()

    class Meta:
        model = Shift
        fields = [
            "id", "rota_week", "store", "employee", "date", "start_time", "end_time",
            "break_minutes", "hours", "role", "status", "notes",
        ]
        read_only_fields = fields

    def get_hours(self, obj):
        return str(shift_hours(obj.date, obj.start_time, obj.end_time, obj.break_minutes))


class MyShiftSerializer(ShiftSerializer):
    """What an employee sees of their own shift. No private manager notes."""

    class Meta(ShiftSerializer.Meta):
        fields = [
            "id", "store", "date", "start_time", "end_time", "break_minutes", "hours", "role", "status",
        ]
        read_only_fields = fields


class _ShiftInputBase(serializers.Serializer):
    employee = serializers.IntegerField(required=False, allow_null=True)
    date = serializers.DateField()
    start_time = serializers.TimeField(required=False)
    end_time = serializers.TimeField(required=False)
    break_minutes = serializers.IntegerField(required=False, min_value=0, max_value=480)
    role = serializers.CharField(max_length=50, required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)

    def validate_employee(self, value):
        if value is None:
            return None
        # Only people this user may see can be rostered, so IDs of other stores' staff are not revealed.
        employee = employees_visible_to(self.context["request"].user).filter(pk=value).first()
        if employee is None:
            raise serializers.ValidationError("This person was not found.")
        return employee


class ShiftCreateSerializer(_ShiftInputBase):
    template = serializers.IntegerField(required=False)

    def validate(self, attrs):
        template_id = attrs.pop("template", None)
        if template_id is not None:
            template = ShiftTemplate.objects.filter(pk=template_id, store=self.context["rota_week"].store).first()
            if template is None or not template.is_active:
                raise serializers.ValidationError({"template": "This template is not available for this store."})
            attrs.setdefault("start_time", template.start_time)
            attrs.setdefault("end_time", template.end_time)
            attrs.setdefault("break_minutes", template.break_minutes)
        if "start_time" not in attrs or "end_time" not in attrs:
            raise serializers.ValidationError("Give a start and end time, or choose a template.")
        attrs.setdefault("break_minutes", 0)
        attrs.setdefault("employee", None)
        return attrs


class ShiftUpdateSerializer(_ShiftInputBase):
    date = serializers.DateField(required=False)


class RotaWeekSerializer(serializers.ModelSerializer):
    store = StoreBriefSerializer(read_only=True)
    scheduled_shifts = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = RotaWeek
        fields = ["id", "store", "week_start", "week_end", "status", "published_at", "scheduled_shifts"]
        read_only_fields = fields

    week_end = serializers.SerializerMethodField()

    def get_week_end(self, obj):
        return obj.week_start + timedelta(days=6)


class RotaWeekDetailSerializer(RotaWeekSerializer):
    shifts = serializers.SerializerMethodField()

    class Meta(RotaWeekSerializer.Meta):
        fields = RotaWeekSerializer.Meta.fields + ["shifts"]
        read_only_fields = fields

    def get_shifts(self, obj):
        return ShiftSerializer(obj.shifts.select_related("rota_week__store", "employee__user"), many=True).data


class RotaWeekCreateSerializer(serializers.Serializer):
    store = serializers.IntegerField()
    week_start = serializers.DateField()
    copy_from_previous = serializers.BooleanField(required=False, default=False)


class MyRotaQuerySerializer(serializers.Serializer):
    from_date = serializers.DateField(required=False)
    to_date = serializers.DateField(required=False)

    def validate(self, attrs):
        start, end = attrs.get("from_date"), attrs.get("to_date")
        if start and end:
            if end < start:
                raise serializers.ValidationError("'to' cannot be before 'from'.")
            if (end - start).days > 92:
                raise serializers.ValidationError("Ask for at most about three months at a time.")
        return attrs
