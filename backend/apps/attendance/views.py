from datetime import timedelta

from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsManagerOrAbove
from apps.employees.models import EmployeeProfile
from apps.rota.models import Shift
from apps.stores.scoping import stores_visible_to

from . import services
from .models import AttendanceRecord, ReviewStatus
from .scoping import attendance_visible_to
from .serializers import (
    AttendanceEditSerializer,
    AttendanceSerializer,
    DecisionSerializer,
    EditSerializer,
    ListQuerySerializer,
    ManualRecordSerializer,
    MyAttendanceSerializer,
)

# Clocking in and out is the separate FastAPI service (attendance_api). This is everything else:
# employees read their own time; managers review and correct it for their own stores only.


def get_record(request, pk):
    return get_object_or_404(attendance_visible_to(request.user), pk=pk)


class MyAttendanceView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        profile = EmployeeProfile.objects.filter(user=request.user).first()
        if profile is None:
            raise NotFound("You do not have an employee profile.")
        query = ListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        end = query.validated_data.get("end") or timezone.localdate()
        start = query.validated_data.get("start") or end - timedelta(days=13)
        records = AttendanceRecord.objects.filter(
            employee=profile, clock_in_at__date__range=(start, end)
        ).select_related("store")
        return Response(MyAttendanceSerializer(records, many=True).data)


class AttendanceListView(APIView):
    permission_classes = [IsManagerOrAbove]

    def get(self, request):
        query = ListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        data = query.validated_data
        end = data.get("end") or timezone.localdate()
        start = data.get("start") or end - timedelta(days=6)
        records = attendance_visible_to(request.user).filter(clock_in_at__date__range=(start, end))
        if "store" in data:
            records = records.filter(store_id=data["store"])
        if "employee" in data:
            records = records.filter(employee_id=data["employee"])
        review = data["review"]
        if review == "flagged":
            records = records.exclude(flags=[])
        elif review != "all":
            records = records.filter(review_status=review)
        return Response(AttendanceSerializer(records.order_by("clock_in_at", "id"), many=True).data)


class AttendanceDetailView(APIView):
    permission_classes = [IsManagerOrAbove]

    def get(self, request, pk):
        return Response(AttendanceSerializer(get_record(request, pk)).data)

    def patch(self, request, pk):
        record = get_record(request, pk)
        body = EditSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        services.edit_record(
            record=record, by=request.user, reason=data["reason"],
            clock_in_at=data.get("clock_in_at"), clock_out_at=data.get("clock_out_at"),
        )
        return Response(AttendanceSerializer(get_record(request, pk)).data)


class AttendanceDecisionView(APIView):
    permission_classes = [IsManagerOrAbove]
    approve = True

    def post(self, request, pk):
        record = get_record(request, pk)
        body = DecisionSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        services.review_record(record=record, by=request.user, approve=self.approve, note=body.validated_data["note"])
        return Response(AttendanceSerializer(get_record(request, pk)).data)


class AttendanceEditsView(APIView):
    permission_classes = [IsManagerOrAbove]

    def get(self, request, pk):
        record = get_record(request, pk)
        edits = record.edits.select_related("edited_by")
        return Response(AttendanceEditSerializer(edits, many=True).data)


class ManualRecordView(APIView):
    permission_classes = [IsManagerOrAbove]

    def post(self, request):
        body = ManualRecordSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        if not stores_visible_to(request.user).filter(pk=data["store"].pk).exists():
            raise PermissionDenied("You do not run this store.")
        shift = None
        if data["shift"] is not None:
            shift = Shift.objects.select_related("rota_week").filter(pk=data["shift"]).first()
            if shift is None:
                return Response({"shift": "That shift does not exist."}, status=status.HTTP_400_BAD_REQUEST)
        record = services.add_manual_record(
            by=request.user, employee=data["employee"], store=data["store"], shift=shift,
            clock_in_at=data["clock_in_at"], clock_out_at=data["clock_out_at"], reason=data["reason"],
        )
        return Response(AttendanceSerializer(get_record(request, record.pk)).data, status=status.HTTP_201_CREATED)
