from django.shortcuts import get_object_or_404
from django.db.models import Q
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsManagerOrAbove
from apps.employees.models import EmployeeProfile

from . import swaps
from .models import OPEN_SWAP_STATUSES, Shift, ShiftSwap, SwapStatus
from .scoping import swaps_visible_to
from .swap_serializers import (
    ManagerSwapSerializer,
    MySwapSerializer,
    PersonSerializer,
    SwapAcceptSerializer,
    SwapRejectSerializer,
    SwapRequestSerializer,
)

# Employees reach only swaps they are part of (/api/me/swaps/). Managers reach swaps at their own
# stores, Owner and HR reach all of them (/api/swaps/).

SWAP_RELATED = (
    "requester__user", "colleague__user", "offered_shift__rota_week__store", "requested_shift__rota_week__store",
)


def my_profile(request):
    profile = EmployeeProfile.objects.filter(user=request.user).select_related("user").first()
    if profile is None:
        raise NotFound("You do not have an employee profile.")
    return profile


def my_swap(profile, pk):
    queryset = ShiftSwap.objects.select_related(*SWAP_RELATED).filter(Q(requester=profile) | Q(colleague=profile))
    return get_object_or_404(queryset, pk=pk)


def mine(swap, profile):
    return Response(MySwapSerializer(swap, context={"me": profile}).data)


class MySwapsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        profile = my_profile(request)
        wanted = request.query_params.get("status")
        queryset = ShiftSwap.objects.select_related(*SWAP_RELATED).filter(Q(requester=profile) | Q(colleague=profile))
        if wanted == "open":
            queryset = queryset.filter(status__in=OPEN_SWAP_STATUSES)
        elif wanted:
            if wanted not in SwapStatus.values:
                return Response({"status": "Use open or one of the swap statuses."}, status=status.HTTP_400_BAD_REQUEST)
            queryset = queryset.filter(status=wanted)
        return Response(MySwapSerializer(queryset, many=True, context={"me": profile}).data)

    def post(self, request):
        profile = my_profile(request)
        body = SwapRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        swap = swaps.request_swap(
            requester=profile, offered_shift_id=body.validated_data["offered_shift"],
            colleague_id=body.validated_data["colleague"], note=body.validated_data["note"],
        )
        swap = my_swap(profile, swap.pk)
        return Response(MySwapSerializer(swap, context={"me": profile}).data, status=status.HTTP_201_CREATED)


class MySwapColleaguesView(APIView):
    """Who could take one of my shifts? Names only: nothing about their hours or paperwork."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        profile = my_profile(request)
        try:
            shift_id = int(request.query_params.get("shift", ""))
        except ValueError:
            return Response({"shift": "Give the id of one of your shifts."}, status=status.HTTP_400_BAD_REQUEST)
        shift = Shift.objects.select_related("rota_week__store").filter(pk=shift_id, employee=profile).first()
        if shift is None:
            raise NotFound()
        return Response(PersonSerializer(swaps.eligible_colleagues(shift), many=True).data)


class MySwapActionView(APIView):
    permission_classes = [IsAuthenticated]
    action = None

    def post(self, request, pk):
        profile = my_profile(request)
        swap = my_swap(profile, pk)
        if self.action == "accept":
            body = SwapAcceptSerializer(data=request.data)
            body.is_valid(raise_exception=True)
            swaps.colleague_accepts(
                swap=swap, employee=profile, in_return_shift_id=body.validated_data["in_return_shift"]
            )
        elif self.action == "decline":
            swaps.colleague_declines(swap=swap, employee=profile)
        else:
            swaps.requester_cancels(swap=swap, employee=profile)
        return mine(my_swap(profile, pk), profile)


class SwapQueueView(APIView):
    """Swaps for managers. Defaults to the ones waiting for a decision."""

    permission_classes = [IsManagerOrAbove]

    def get(self, request):
        wanted = request.query_params.get("status", SwapStatus.PENDING_MANAGER)
        queryset = swaps_visible_to(request.user)
        if wanted != "all":
            if wanted not in SwapStatus.values:
                return Response({"status": "Use a swap status or all."}, status=status.HTTP_400_BAD_REQUEST)
            queryset = queryset.filter(status=wanted)
        return Response(ManagerSwapSerializer(queryset.order_by("created_at", "id"), many=True).data)


class SwapDecisionView(APIView):
    permission_classes = [IsManagerOrAbove]
    action = None

    def post(self, request, pk):
        swap = get_object_or_404(swaps_visible_to(request.user), pk=pk)
        if self.action == "approve":
            swaps.manager_approves(swap=swap, by=request.user)
        else:
            body = SwapRejectSerializer(data=request.data)
            body.is_valid(raise_exception=True)
            swaps.manager_rejects(swap=swap, by=request.user, reason=body.validated_data["reason"])
        return Response(ManagerSwapSerializer(get_object_or_404(swaps_visible_to(request.user), pk=pk)).data)
