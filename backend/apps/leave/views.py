from datetime import timedelta

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsManagerOrAbove, IsOwnerOrHR
from apps.employees.models import EmployeeProfile
from apps.rota.swap_views import my_profile

from . import services
from .models import LeaveRequest, LeaveStatus
from .scoping import leave_visible_to
from .serializers import (
    AllowanceSerializer,
    ApproveSerializer,
    CalendarEntrySerializer,
    DateQuerySerializer,
    LeaveCreateSerializer,
    ManagerLeaveSerializer,
    MyLeaveSerializer,
    RangeQuerySerializer,
    ReasonSerializer,
)

MAX_CALENDAR_DAYS = 62


def balance_data(info):
    return {key: (value.isoformat() if hasattr(value, "isoformat") else value) for key, value in info.items()}


# --- Employees: /api/me/leave/ ----------------------------------------------------------------

class MyLeaveView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        profile = my_profile(request)
        queryset = LeaveRequest.objects.filter(employee=profile)
        wanted = request.query_params.get("status")
        if wanted:
            if wanted not in LeaveStatus.values:
                return Response({"status": "Use a leave status."}, status=status.HTTP_400_BAD_REQUEST)
            queryset = queryset.filter(status=wanted)
        return Response(MyLeaveSerializer(queryset.order_by("-start_date", "-id"), many=True).data)

    def post(self, request):
        profile = my_profile(request)
        body = LeaveCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        leave = services.request_leave(employee=profile, **body.validated_data)
        return Response(MyLeaveSerializer(leave).data, status=status.HTTP_201_CREATED)


class MyLeaveBalanceView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        profile = my_profile(request)
        query = DateQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        on = query.validated_data.get("date") or timezone.localdate()
        return Response(balance_data(services.balance(profile, on)))


class MyLeaveCancelView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        profile = my_profile(request)
        leave = get_object_or_404(LeaveRequest.objects.filter(employee=profile), pk=pk)
        leave = services.cancel_own(leave=leave, user=request.user)
        return Response(MyLeaveSerializer(leave).data)


# --- Managers, HR and the Owner: /api/leave/ --------------------------------------------------

class LeaveQueueView(APIView):
    """Leave for managers. Defaults to the requests waiting for a decision."""

    permission_classes = [IsManagerOrAbove]

    def get(self, request):
        wanted = request.query_params.get("status", LeaveStatus.PENDING)
        queryset = leave_visible_to(request.user)
        if wanted != "all":
            if wanted not in LeaveStatus.values:
                return Response({"status": "Use a leave status or all."}, status=status.HTTP_400_BAD_REQUEST)
            queryset = queryset.filter(status=wanted)
        return Response(ManagerLeaveSerializer(queryset.order_by("start_date", "id"), many=True).data)


class LeaveDecisionView(APIView):
    permission_classes = [IsManagerOrAbove]
    action = None

    def post(self, request, pk):
        leave = get_object_or_404(leave_visible_to(request.user), pk=pk)
        if self.action == "approve":
            body = ApproveSerializer(data=request.data)
            body.is_valid(raise_exception=True)
            services.approve(leave=leave, by=request.user, days_charged=body.validated_data.get("days_charged"))
        else:
            body = ReasonSerializer(data=request.data)
            body.is_valid(raise_exception=True)
            act = services.reject if self.action == "reject" else services.cancel_by_manager
            act(leave=leave, by=request.user, reason=body.validated_data["reason"])
        return Response(ManagerLeaveSerializer(get_object_or_404(leave_visible_to(request.user), pk=pk)).data)


class LeaveCalendarView(APIView):
    """Who is off between two dates, so a manager can plan the rota. Approved leave only, kind not shown."""

    permission_classes = [IsManagerOrAbove]

    def get(self, request):
        body = RangeQuerySerializer(data=request.query_params)
        body.is_valid(raise_exception=True)
        start, end = body.validated_data["start"], body.validated_data["end"]
        if end < start or end - start > timedelta(days=MAX_CALENDAR_DAYS):
            return Response({"end": f"Ask for at most {MAX_CALENDAR_DAYS} days, ending after the start."},
                            status=status.HTTP_400_BAD_REQUEST)
        entries = leave_visible_to(request.user).filter(
            status=LeaveStatus.APPROVED, start_date__lte=end, end_date__gte=start,
        )
        return Response(CalendarEntrySerializer(entries.order_by("start_date", "id"), many=True).data)


class EmployeeLeaveAllowanceView(APIView):
    """HR sets how many annual-leave days someone has for a leave year."""

    permission_classes = [IsOwnerOrHR]

    def get(self, request, employee_id):
        employee = get_object_or_404(EmployeeProfile, pk=employee_id)
        query = DateQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        on = query.validated_data.get("date") or timezone.localdate()
        return Response(balance_data(services.balance(employee, on)))

    def put(self, request, employee_id):
        employee = get_object_or_404(EmployeeProfile, pk=employee_id)
        body = AllowanceSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        on = body.validated_data.get("date") or timezone.localdate()
        info = services.set_allowance(employee=employee, on=on, days=body.validated_data["days"], by=request.user)
        return Response(balance_data(info))
