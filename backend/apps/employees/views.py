from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.permissions import IsManagerOrAbove, IsOwnerOrHR

from . import services
from .scoping import cover_requests_visible_to, employees_visible_to
from .serializers import (
    CoverRequestCreateSerializer,
    CoverRequestSerializer,
    EmployeeCreateSerializer,
    EmployeeSerializer,
    EmployeeUpdateSerializer,
    RejectSerializer,
    TransferSerializer,
)


class EmployeeViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Employee profiles. Owner/HR manage them; managers see their store's staff; employees see themselves."""

    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        return employees_visible_to(self.request.user)

    def get_serializer_class(self):
        if self.action == "create":
            return EmployeeCreateSerializer
        if self.action == "partial_update":
            return EmployeeUpdateSerializer
        if self.action == "transfer":
            return TransferSerializer
        return EmployeeSerializer

    def get_permissions(self):
        if self.action in {"create", "partial_update", "transfer"}:
            return [IsOwnerOrHR()]
        return [IsAuthenticated()]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profile = serializer.save()
        return Response(EmployeeSerializer(profile).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(EmployeeSerializer(instance).data)

    @action(detail=False, methods=["get"])
    def me(self, request):
        """The signed-in user's own employee profile."""
        profile = employees_visible_to(request.user).filter(user=request.user).first()
        if profile is None:
            raise NotFound("You do not have an employee profile.")
        return Response(EmployeeSerializer(profile).data)

    @action(detail=True, methods=["post"])
    def transfer(self, request, pk=None):
        """Move the employee's home store from a date (default today)."""
        employee = self.get_object()
        serializer = TransferSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.transfer_home_store(
            employee=employee,
            new_store=serializer.validated_data["store"],
            effective_date=serializer.validated_data.get("effective_date") or timezone.localdate(),
            by=request.user,
        )
        return Response(EmployeeSerializer(self.get_queryset().get(pk=employee.pk)).data)


class CoverRequestViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """Requests for an employee to work at another store. Both stores' managers must approve."""

    serializer_class = CoverRequestSerializer
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        return cover_requests_visible_to(self.request.user)

    def get_permissions(self):
        if self.action in {"create", "approve", "reject", "cancel"}:
            return [IsManagerOrAbove()]
        return [IsAuthenticated()]

    def create(self, request, *args, **kwargs):
        serializer = CoverRequestCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        assignment = serializer.save()
        return Response(CoverRequestSerializer(assignment).data, status=status.HTTP_201_CREATED)

    def _respond(self, pk):
        return Response(CoverRequestSerializer(self.get_queryset().get(pk=pk)).data)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        assignment = self.get_object()
        services.approve_cover(assignment.pk, request.user)
        return self._respond(assignment.pk)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        assignment = self.get_object()
        body = RejectSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        services.reject_cover(assignment.pk, request.user, body.validated_data["reason"])
        return self._respond(assignment.pk)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        assignment = self.get_object()
        services.cancel_cover(assignment.pk, request.user)
        return self._respond(assignment.pk)
