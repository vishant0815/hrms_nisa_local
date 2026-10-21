from django.utils import timezone
from rest_framework import serializers

from apps.accounts.models import Role, User
from apps.stores.models import Store
from apps.stores.serializers import StoreBriefSerializer

from . import services
from .models import EmployeeProfile, StoreAssignment


class EmployeeUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "email", "first_name", "last_name", "role"]
        read_only_fields = fields


class EmployeeSerializer(serializers.ModelSerializer):
    user = EmployeeUserSerializer(read_only=True)
    home_store = serializers.SerializerMethodField()
    is_current = serializers.SerializerMethodField()

    class Meta:
        model = EmployeeProfile
        fields = [
            "id", "employee_number", "user", "phone", "job_title",
            "start_date", "end_date", "home_store", "is_current",
        ]
        read_only_fields = fields

    def get_home_store(self, obj):
        prefetched = getattr(obj, "current_home", None)
        if prefetched is not None:
            store = prefetched[0].store if prefetched else None
        else:
            store = services.home_store_on(obj, timezone.localdate())
        return StoreBriefSerializer(store).data if store else None

    def get_is_current(self, obj):
        return obj.is_current()


class EmployeeBriefSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()

    class Meta:
        model = EmployeeProfile
        fields = ["id", "employee_number", "name"]
        read_only_fields = fields

    def get_name(self, obj):
        return obj.user.get_full_name() or obj.user.email


class EmployeeCreateSerializer(serializers.Serializer):
    user = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(role__in=[Role.MANAGER, Role.EMPLOYEE], is_active=True)
    )
    home_store = serializers.PrimaryKeyRelatedField(queryset=Store.objects.filter(is_active=True))
    start_date = serializers.DateField()
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True, default="")
    job_title = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")

    def validate_user(self, value):
        if EmployeeProfile.objects.filter(user=value).exists():
            raise serializers.ValidationError("This user already has an employee profile.")
        return value

    def create(self, validated_data):
        return services.create_employee(created_by=self.context["request"].user, **validated_data)


class EmployeeUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmployeeProfile
        fields = ["phone", "job_title", "end_date"]

    def validate_end_date(self, value):
        if value is not None and value < self.instance.start_date:
            raise serializers.ValidationError("The end date cannot be before the start date.")
        return value


class TransferSerializer(serializers.Serializer):
    store = serializers.PrimaryKeyRelatedField(queryset=Store.objects.all())
    effective_date = serializers.DateField(required=False)


class CoverRequestSerializer(serializers.ModelSerializer):
    employee = EmployeeBriefSerializer(read_only=True)
    store = StoreBriefSerializer(read_only=True)
    home_store = StoreBriefSerializer(read_only=True)

    class Meta:
        model = StoreAssignment
        fields = [
            "id", "employee", "store", "home_store", "valid_from", "valid_to", "status", "reason",
            "requested_by", "home_approved_by", "home_approved_at",
            "destination_approved_by", "destination_approved_at",
            "rejected_by", "rejection_reason", "created_at",
        ]
        read_only_fields = fields


class CoverRequestCreateSerializer(serializers.Serializer):
    employee = serializers.PrimaryKeyRelatedField(queryset=EmployeeProfile.objects.select_related("user"))
    store = serializers.PrimaryKeyRelatedField(queryset=Store.objects.all())
    valid_from = serializers.DateField()
    valid_to = serializers.DateField()
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")

    def create(self, validated_data):
        return services.request_cover(requested_by=self.context["request"].user, **validated_data)


class RejectSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
