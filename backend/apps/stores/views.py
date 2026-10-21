from django.db import transaction
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.permissions import IsOwnerOrHR

from .models import StoreHours
from .scoping import stores_visible_to
from .serializers import StoreHoursSerializer, StoreSerializer


class StoreViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Stores. Owner and HR manage them; managers and employees see only their own.

    There is no delete: set is_active=false to retire a store.
    """

    serializer_class = StoreSerializer
    http_method_names = ["get", "post", "put", "patch", "head", "options"]

    def get_queryset(self):
        return stores_visible_to(self.request.user).prefetch_related("managers")

    def get_permissions(self):
        writing = self.action in {"create", "update", "partial_update"}
        replacing_hours = self.action == "hours" and self.request.method == "PUT"
        if writing or replacing_hours:
            return [IsOwnerOrHR()]
        return [IsAuthenticated()]

    @action(detail=True, methods=["get", "put"])
    def hours(self, request, pk=None):
        """GET the opening hours, or PUT a full replacement (up to 7 weekdays)."""
        store = self.get_object()
        if request.method == "GET":
            return Response(StoreHoursSerializer(store.hours.all(), many=True).data)

        serializer = StoreHoursSerializer(data=request.data, many=True)
        serializer.is_valid(raise_exception=True)
        weekdays = [item["weekday"] for item in serializer.validated_data]
        if len(weekdays) != len(set(weekdays)):
            return Response({"detail": "Each weekday may appear only once."}, status=400)
        with transaction.atomic():
            store.hours.all().delete()
            StoreHours.objects.bulk_create(
                [StoreHours(store=store, **item) for item in serializer.validated_data]
            )
        return Response(StoreHoursSerializer(store.hours.all(), many=True).data)
