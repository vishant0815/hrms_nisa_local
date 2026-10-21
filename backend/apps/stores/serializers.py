from rest_framework import serializers

from apps.accounts.models import Role, User

from .models import Store, StoreHours


class StoreSerializer(serializers.ModelSerializer):
    managers = serializers.PrimaryKeyRelatedField(
        many=True, required=False, queryset=User.objects.filter(role=Role.MANAGER)
    )

    class Meta:
        model = Store
        fields = [
            "id", "name", "address", "phone", "is_active",
            "latitude", "longitude", "radius_m", "managers", "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def validate(self, attrs):
        latitude = attrs.get("latitude", getattr(self.instance, "latitude", None))
        longitude = attrs.get("longitude", getattr(self.instance, "longitude", None))
        if (latitude is None) != (longitude is None):
            raise serializers.ValidationError("Latitude and longitude must be set together.")
        return attrs


class StoreBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Store
        fields = ["id", "name"]
        read_only_fields = fields


class StoreHoursSerializer(serializers.ModelSerializer):
    class Meta:
        model = StoreHours
        fields = ["weekday", "is_closed", "opens", "closes"]

    def validate(self, attrs):
        if attrs.get("is_closed"):
            return {**attrs, "opens": None, "closes": None}
        opens, closes = attrs.get("opens"), attrs.get("closes")
        if opens is None or closes is None:
            raise serializers.ValidationError("Opening and closing times are required for an open day.")
        if opens == closes:
            raise serializers.ValidationError("Opening and closing times cannot be the same.")
        return attrs
