from datetime import timedelta

from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsManagerOrAbove
from apps.employees.models import EmployeeProfile
from apps.stores.scoping import stores_visible_to

from . import services
from .models import Shift, ShiftStatus, ShiftTemplate, WeekStatus
from .scoping import rota_weeks_visible_to, shifts_visible_to, templates_visible_to
from .serializers import (
    MyRotaQuerySerializer,
    MyShiftSerializer,
    RotaWeekCreateSerializer,
    RotaWeekDetailSerializer,
    RotaWeekSerializer,
    ShiftCreateSerializer,
    ShiftSerializer,
    ShiftTemplateCreateSerializer,
    ShiftTemplateSerializer,
    ShiftUpdateSerializer,
)

# Rota management is for Owner, HR and the managers of each store. Managers only ever reach
# their own store's rota (the querysets are scoped). Employees use /api/me/rota/.


def with_counts(weeks):
    return weeks.annotate(
        scheduled_shifts=Count("shifts", filter=Q(shifts__status=ShiftStatus.SCHEDULED))
    )


def shift_payload(shift, warnings):
    """A shift as the API shows it, plus any warnings from saving it."""
    shift = Shift.objects.select_related("rota_week__store", "employee__user").get(pk=shift.pk)
    return {**ShiftSerializer(shift).data, "warnings": warnings}


class ShiftTemplateViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Reusable shift patterns per store. Switched off with is_active, never deleted."""

    serializer_class = ShiftTemplateSerializer
    permission_classes = [IsManagerOrAbove]

    def get_queryset(self):
        templates = templates_visible_to(self.request.user)
        store = self.request.query_params.get("store")
        return templates.filter(store_id=store) if store and store.isdigit() else templates

    def create(self, request):
        serializer = ShiftTemplateCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        store = get_object_or_404(stores_visible_to(request.user), pk=data.pop("store"))
        if ShiftTemplate.objects.filter(store=store, name=data["name"]).exists():
            return Response({"name": ["This store already has a template with that name."]}, status=400)
        template = ShiftTemplate.objects.create(store=store, **data)
        return Response(ShiftTemplateSerializer(template).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, pk=None):
        template = self.get_object()
        serializer = ShiftTemplateSerializer(template, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class RotaWeekViewSet(viewsets.GenericViewSet):
    permission_classes = [IsManagerOrAbove]

    def get_queryset(self):
        return with_counts(rota_weeks_visible_to(self.request.user))

    def list(self, request):
        """Weeks I may see. Filter with ?store= and ?week_start=. The owner can ask for one
        week across all stores (?week_start=...&with_shifts=1) to see every shop at once."""
        weeks = self.get_queryset()
        params = request.query_params
        if params.get("store", "").isdigit():
            weeks = weeks.filter(store_id=int(params["store"]))
        if params.get("week_start"):
            weeks = weeks.filter(week_start=params["week_start"])
        serializer_class = RotaWeekDetailSerializer if params.get("with_shifts") == "1" else RotaWeekSerializer
        return Response(serializer_class(weeks, many=True).data)

    def retrieve(self, request, pk=None):
        return Response(RotaWeekDetailSerializer(self.get_object()).data)

    def create(self, request):
        body = RotaWeekCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        store = get_object_or_404(stores_visible_to(request.user), pk=body.validated_data["store"])
        week, summary = services.create_week(
            store=store,
            week_start=body.validated_data["week_start"],
            by=request.user,
            copy_from_previous=body.validated_data["copy_from_previous"],
        )
        data = RotaWeekDetailSerializer(self.get_queryset().get(pk=week.pk)).data
        return Response({**data, "copy_summary": summary}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        week = self.get_object()
        services.publish_week(rota_week=week, by=request.user)
        return Response(RotaWeekDetailSerializer(self.get_queryset().get(pk=week.pk)).data)

    @action(detail=True, methods=["post"], url_path="shifts", url_name="shifts")
    def add_shift(self, request, pk=None):
        week = self.get_object()
        serializer = ShiftCreateSerializer(data=request.data, context={"request": request, "rota_week": week})
        serializer.is_valid(raise_exception=True)
        shift, warnings = services.create_shift(rota_week=week, by=request.user, **serializer.validated_data)
        return Response(shift_payload(shift, warnings), status=status.HTTP_201_CREATED)


class ShiftViewSet(mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = ShiftSerializer
    permission_classes = [IsManagerOrAbove]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        return shifts_visible_to(self.request.user)

    def partial_update(self, request, pk=None):
        shift = self.get_object()
        serializer = ShiftUpdateSerializer(data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        shift, warnings = services.update_shift(shift=shift, by=request.user, **serializer.validated_data)
        return Response(shift_payload(shift, warnings))

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        shift = self.get_object()
        shift = services.cancel_shift(shift=shift, by=request.user)
        return Response(shift_payload(shift, []))


class MyRotaView(APIView):
    """My own shifts, from published rotas only. Drafts and other people's shifts are never shown."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        profile = EmployeeProfile.objects.filter(user=request.user).first()
        if profile is None:
            raise NotFound("You do not have an employee profile.")
        params = request.query_params
        query = MyRotaQuerySerializer(
            data={k: v for k, v in {"from_date": params.get("from"), "to_date": params.get("to")}.items() if v}
        )
        query.is_valid(raise_exception=True)
        start = query.validated_data.get("from_date") or timezone.localdate()
        end = query.validated_data.get("to_date") or (start + timedelta(days=13))
        shifts = (
            Shift.objects.filter(
                employee=profile,
                rota_week__status=WeekStatus.PUBLISHED,
                date__range=(start, end),
            )
            .select_related("rota_week__store")
            .order_by("date", "start_time", "id")
        )
        return Response(MyShiftSerializer(shifts, many=True).data)
