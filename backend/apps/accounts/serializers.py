from django.contrib.auth import password_validation
from rest_framework import serializers

from .models import Role, User


class UserSerializer(serializers.ModelSerializer):
    """Read-only view of an account. Never includes the password hash."""

    class Meta:
        model = User
        fields = ["id", "email", "first_name", "last_name", "role", "is_active", "date_joined"]
        read_only_fields = fields


def _check_role_assignable(request_user, role):
    """Only an Owner may create or promote another Owner. HR cannot escalate privileges."""
    if role == Role.OWNER and request_user.role != Role.OWNER:
        raise serializers.ValidationError("Only an owner can assign the owner role.")
    return role


class UserCreateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, style={"input_type": "password"})

    class Meta:
        model = User
        fields = ["id", "email", "first_name", "last_name", "role", "password"]
        read_only_fields = ["id"]

    def validate_email(self, value):
        value = value.lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value

    def validate_role(self, value):
        return _check_role_assignable(self.context["request"].user, value)

    def validate(self, attrs):
        candidate = User(
            email=attrs.get("email"),
            first_name=attrs.get("first_name", ""),
            last_name=attrs.get("last_name", ""),
        )
        password_validation.validate_password(attrs["password"], user=candidate)
        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password")
        return User.objects.create_user(password=password, **validated_data)


class UserUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["first_name", "last_name", "role", "is_active"]

    def validate_role(self, value):
        return _check_role_assignable(self.context["request"].user, value)

    def validate(self, attrs):
        target = self.instance
        actor = self.context["request"].user
        # HR cannot modify an existing owner account in any way.
        if target.role == Role.OWNER and actor.role != Role.OWNER:
            raise serializers.ValidationError("Only an owner can modify an owner account.")
        # Nobody can lock themselves out by deactivating or demoting their own account.
        if target.pk == actor.pk and (
            attrs.get("is_active") is False or ("role" in attrs and attrs["role"] != target.role)
        ):
            raise serializers.ValidationError(
                "You cannot deactivate or change the role of your own account."
            )
        return attrs


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)

    def validate_old_password(self, value):
        if not self.context["request"].user.check_password(value):
            raise serializers.ValidationError("Current password is incorrect.")
        return value

    def validate_new_password(self, value):
        password_validation.validate_password(value, user=self.context["request"].user)
        return value
