from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Role
from apps.accounts.permissions import IsManagerOrAbove, IsOwnerOrHR
from apps.employees.models import EmployeeProfile
from apps.employees.scoping import employees_visible_to

from . import services
from .models import EmployeeVisa, RightToWorkCheck, VisaType
from .rules import check_right_to_work
from .serializers import (
    CanWorkQuerySerializer,
    EmployeeVisaCreateSerializer,
    EmployeeVisaSerializer,
    EmployeeVisaUpdateSerializer,
    RevokeSerializer,
    RightToWorkCheckSerializer,
    VacationPeriodSerializer,
    VisaTypeSerializer,
)

# Every immigration endpoint below is Owner/HR only, except can-work which gives managers a
# yes/no. Managers and employees never see visa types, dates or documents.


def with_checks(queryset):
    """Load each visa's checks once, newest first, so status needs no per-row query."""
    return queryset.select_related("visa_type", "employee__user").prefetch_related(
        Prefetch(
            "checks",
            queryset=RightToWorkCheck.objects.order_by("-checked_on", "-id"),
            to_attr="checks_desc",
        )
    )


def get_employee(employee_id):
    return get_object_or_404(EmployeeProfile.objects.select_related("user"), pk=employee_id)


def get_visa(pk):
    return get_object_or_404(with_checks(EmployeeVisa.objects.all()), pk=pk)


class VisaTypeViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """The editable rules table. Types are switched off (is_active), never deleted."""

    queryset = VisaType.objects.all()
    serializer_class = VisaTypeSerializer
    permission_classes = [IsOwnerOrHR]
    http_method_names = ["get", "post", "patch", "head", "options"]


class EmployeeVisasView(APIView):
    permission_classes = [IsOwnerOrHR]

    def get(self, request, employee_id):
        employee = get_employee(employee_id)
        visas = with_checks(EmployeeVisa.objects.filter(employee=employee))
        services.audit(request.user, "visa_viewed", employee, detail="history")
        return Response(EmployeeVisaSerializer(visas, many=True).data)

    def post(self, request, employee_id):
        employee = get_employee(employee_id)
        serializer = EmployeeVisaCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        visa = services.add_visa(employee=employee, by=request.user, **serializer.validated_data)
        return Response(EmployeeVisaSerializer(get_visa(visa.pk)).data, status=status.HTTP_201_CREATED)


class VisaDetailView(APIView):
    permission_classes = [IsOwnerOrHR]

    def get(self, request, pk):
        visa = get_visa(pk)
        services.audit(request.user, "visa_viewed", visa.employee, visa, "detail")
        return Response(EmployeeVisaSerializer(visa).data)

    def patch(self, request, pk):
        visa = get_visa(pk)
        serializer = EmployeeVisaUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.update_visa(visa=visa, by=request.user, **serializer.validated_data)
        return Response(EmployeeVisaSerializer(get_visa(pk)).data)


class VisaRevokeView(APIView):
    permission_classes = [IsOwnerOrHR]

    def post(self, request, pk):
        visa = get_visa(pk)
        body = RevokeSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        services.revoke_visa(visa=visa, by=request.user, reason=body.validated_data["reason"])
        return Response(EmployeeVisaSerializer(get_visa(pk)).data)


class VisaVacationsView(APIView):
    permission_classes = [IsOwnerOrHR]

    def get(self, request, pk):
        visa = get_visa(pk)
        return Response(VacationPeriodSerializer(visa.vacations.all(), many=True).data)

    def post(self, request, pk):
        visa = get_visa(pk)
        serializer = VacationPeriodSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        vacation = services.add_vacation(visa=visa, by=request.user, **serializer.validated_data)
        return Response(VacationPeriodSerializer(vacation).data, status=status.HTTP_201_CREATED)


class EmployeeChecksView(APIView):
    """The right-to-work audit trail. Add-only: a correction is a new check."""

    permission_classes = [IsOwnerOrHR]

    def get(self, request, employee_id):
        employee = get_employee(employee_id)
        return Response(RightToWorkCheckSerializer(employee.rtw_checks.all(), many=True).data)

    def post(self, request, employee_id):
        employee = get_employee(employee_id)
        serializer = RightToWorkCheckSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        data.setdefault("checked_on", timezone.localdate())
        check = services.record_check(employee=employee, by=request.user, **data)
        return Response(RightToWorkCheckSerializer(check).data, status=status.HTTP_201_CREATED)


class ExpiringVisasView(APIView):
    """HR dashboard: statuses expiring soon, plus any that have already expired."""

    permission_classes = [IsOwnerOrHR]

    def get(self, request):
        try:
            days = int(request.query_params.get("days", 60))
        except ValueError:
            return Response({"days": "Enter a whole number of days."}, status=status.HTTP_400_BAD_REQUEST)
        if not 1 <= days <= 365:
            return Response({"days": "Use a number between 1 and 365."}, status=status.HTTP_400_BAD_REQUEST)
        visas = with_checks(services.expiring_visas(days))
        return Response(EmployeeVisaSerializer(visas, many=True).data)


class CanWorkView(APIView):
    """Can this employee work on a date? Owner/HR get the detail; managers get yes/no and the hours limit."""

    permission_classes = [IsManagerOrAbove]

    def get(self, request, employee_id):
        employee = employees_visible_to(request.user).filter(pk=employee_id).first()
        if employee is None:
            raise NotFound()
        query = CanWorkQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        on = query.validated_data.get("date") or timezone.localdate()
        decision = check_right_to_work(employee, on, query.validated_data.get("week_hours"))

        if request.user.role in (Role.OWNER, Role.HR):
            body = decision.for_hr()
        else:
            body = decision.for_manager()
        return Response({"employee": employee.pk, "date": on, **body})
