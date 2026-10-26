from datetime import timedelta

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsManagerOrAbove, IsOwnerOrHR
from apps.attendance.scoping import attendance_visible_to
from apps.attendance.serializers import AttendanceSerializer
from apps.employees.serializers import EmployeeBriefSerializer
from apps.stores.serializers import StoreBriefSerializer

from . import report, services
from .models import ClockDevice, Enrolment
from .scoping import devices_visible_to, punches_visible_to
from .serializers import (
    BreakBasisSerializer,
    BreakReportQuerySerializer,
    ClockDeviceSerializer,
    DeviceCreateSerializer,
    DeviceUpdateSerializer,
    EnrolmentSerializer,
    EnrolSerializer,
    PunchQuerySerializer,
    PunchSerializer,
    ReasonSerializer,
)

MAX_REPORT_DAYS = 62


# --- Devices --------------------------------------------------------------------------------

class DeviceListView(APIView):
    """Managers see the devices at their shops (and whether they are online). Owner/HR register them."""

    def get_permissions(self):
        return [IsOwnerOrHR() if self.request.method == "POST" else IsManagerOrAbove()]

    def get(self, request):
        return Response(ClockDeviceSerializer(devices_visible_to(request.user), many=True).data)

    def post(self, request):
        body = DeviceCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        device, secret = services.register_device(**body.validated_data)
        data = ClockDeviceSerializer(device).data
        data["secret"] = secret  # shown once: only a hash is kept
        return Response(data, status=status.HTTP_201_CREATED)


class DeviceDetailView(APIView):
    permission_classes = [IsOwnerOrHR]

    def patch(self, request, pk):
        device = get_object_or_404(ClockDevice, pk=pk)
        body = DeviceUpdateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        for field, value in body.validated_data.items():
            setattr(device, field, value)
        device.save()
        return Response(ClockDeviceSerializer(device).data)


class DeviceSecretView(APIView):
    permission_classes = [IsOwnerOrHR]

    def post(self, request, pk):
        device = get_object_or_404(ClockDevice, pk=pk)
        return Response({"secret": services.rotate_secret(device)})


# --- Enrolment (Owner / HR) -------------------------------------------------------------------

class EnrolmentListView(APIView):
    permission_classes = [IsOwnerOrHR]

    def get(self, request):
        rows = Enrolment.objects.select_related("employee__user")
        if request.query_params.get("active") == "true":
            rows = rows.filter(withdrawn_at__isnull=True)
        return Response(EnrolmentSerializer(rows, many=True).data)

    def post(self, request):
        body = EnrolSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        enrolment = services.enrol(
            employee=body.validated_data["employee"], by=request.user, consent_note=body.validated_data["consent_note"],
        )
        return Response(EnrolmentSerializer(enrolment).data, status=status.HTTP_201_CREATED)


class EnrolmentWithdrawView(APIView):
    permission_classes = [IsOwnerOrHR]

    def post(self, request, pk):
        enrolment = get_object_or_404(Enrolment, pk=pk)
        body = ReasonSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        services.withdraw(enrolment=enrolment, by=request.user, reason=body.validated_data["reason"])
        return Response(EnrolmentSerializer(Enrolment.objects.get(pk=pk)).data)


# --- Punch log and breaks (managers) ----------------------------------------------------------

class PunchListView(APIView):
    permission_classes = [IsManagerOrAbove]

    def get(self, request):
        query = PunchQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        data = query.validated_data
        end = data.get("end") or timezone.localdate()
        start = data.get("start") or end - timedelta(days=6)
        punches = punches_visible_to(request.user).filter(received_at__date__range=(start, end))
        if "device" in data:
            punches = punches.filter(device_id=data["device"])
        if "employee" in data:
            punches = punches.filter(employee_id=data["employee"])
        if data["outcome"] != "all":
            punches = punches.filter(outcome=data["outcome"])
        return Response(PunchSerializer(punches.order_by("received_at", "id"), many=True).data)


class BreakReportView(APIView):
    """Break taken, paid and unpaid, per shift and per person. Managers: their own shops only."""

    permission_classes = [IsManagerOrAbove]

    def get(self, request):
        query = BreakReportQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        data = query.validated_data
        end = data.get("end") or timezone.localdate()
        start = data.get("start") or end - timedelta(days=6)
        if end < start or (end - start).days >= MAX_REPORT_DAYS:
            return Response({"end": f"Ask for at most {MAX_REPORT_DAYS} days, ending after the start."},
                            status=status.HTTP_400_BAD_REQUEST)
        records = attendance_visible_to(request.user).filter(
            clock_in_at__date__range=(start, end), clock_out_at__isnull=False,
        ).select_related("shift").prefetch_related("breaks")
        if "store" in data:
            records = records.filter(store_id=data["store"])
        if "employee" in data:
            records = records.filter(employee_id=data["employee"])
        rows, people = report.build(records.order_by("clock_in_at", "id"), timezone.now())

        def person(item):
            return {**item, "employee": EmployeeBriefSerializer(item["employee"]).data}

        return Response({
            "start": start, "end": end,
            "people": [person(item) for item in people],
            "shifts": [
                {**item, "employee": EmployeeBriefSerializer(item["employee"]).data, "store": StoreBriefSerializer(item["store"]).data}
                for item in rows
            ],
        })


class BreakBasisView(APIView):
    """A manager decides which length counts for the break allowance when the two disagree."""

    permission_classes = [IsManagerOrAbove]

    def post(self, request, pk):
        record = get_object_or_404(attendance_visible_to(request.user), pk=pk)
        body = BreakBasisSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        services.set_break_basis(
            record=record, by=request.user, basis=body.validated_data["basis"], reason=body.validated_data["reason"],
        )
        return Response(AttendanceSerializer(get_object_or_404(attendance_visible_to(request.user), pk=pk)).data)
