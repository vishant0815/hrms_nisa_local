"""Shift swaps: A offers a shift to B, B agrees (and may give a shift back), a manager approves.

Rules:
  - only your own, published, not-yet-started shift can be offered, and only to someone who works
    at that store on that date
  - the colleague must agree, and may give back one of their own shifts at the same store
  - a manager approves; only then do the shifts change hands, in one step, after every rota rule
    is checked again for both people (right to work, hours cap, double-booking, store assignment)
  - employees are told only that a swap "can't go ahead"; the reasons (which could expose a
    colleague's immigration status or hours) go to the managers, in the same manager-safe words
    the rota already uses
"""
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.employees.models import AssignmentStatus, EmployeeProfile, StoreAssignment

from .models import OPEN_SWAP_STATUSES, Shift, ShiftStatus, ShiftSwap, SwapStatus, WeekStatus
from .services import evaluate_assignment, lock_employee, problem, raise_blocks, shift_span

MAX_OPEN_REQUESTS = 5


def fail(code, message):
    raise_blocks([problem(code, message)])


def not_possible():
    fail("swap_not_possible", "This swap can't go ahead. Please speak to your manager.")


def _now():
    return timezone.localtime().replace(tzinfo=None)


def person_name(employee):
    return employee.user.get_full_name() or employee.employee_number


# --- Helpers --------------------------------------------------------------------------------

def eligible_colleagues(shift):
    """People who work at the shift's store on its date, other than the person who holds it."""
    store, day = shift.rota_week.store, shift.date
    assigned = StoreAssignment.objects.filter(
        store=store, status=AssignmentStatus.APPROVED, valid_from__lte=day,
    ).filter(Q(valid_to__isnull=True) | Q(valid_to__gte=day))
    return (
        EmployeeProfile.objects.filter(
            pk__in=assigned.values("employee_id"), start_date__lte=day, user__is_active=True,
        )
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=day))
        .exclude(pk=shift.employee_id)
        .select_related("user")
        .order_by("employee_number")
    )


def _lock_shifts(*ids):
    ids = sorted({i for i in ids if i is not None})
    locked = Shift.objects.select_for_update(of=("self",)).select_related("rota_week__store").filter(pk__in=ids)
    return {shift.pk: shift for shift in locked.order_by("pk")}


def _usable(shift, owner):
    """Still the owner's, still scheduled, on a published rota, and not yet started."""
    return (
        shift is not None
        and shift.status == ShiftStatus.SCHEDULED
        and shift.employee_id == owner.pk
        and shift.rota_week.status == WeekStatus.PUBLISHED
        and shift_span(shift.date, shift.start_time, shift.end_time)[0] > _now()
    )


def _in_other_open_swap(shift, swap=None):
    clash = ShiftSwap.objects.filter(status__in=OPEN_SWAP_STATUSES).filter(
        Q(offered_shift=shift) | Q(requested_shift=shift)
    )
    if swap is not None:
        clash = clash.exclude(pk=swap.pk)
    return clash.exists()


def rule_problems(offered, requested, requester, colleague):
    """Every rota rule, for both people as they would be after the swap. Manager-safe wording."""
    found = []
    for taker, shift, giving_up in ((colleague, offered, requested), (requester, requested, offered)):
        if shift is None:
            continue
        issues, _ = evaluate_assignment(
            store=shift.rota_week.store, employee=taker, day=shift.date, start=shift.start_time,
            end=shift.end_time, break_minutes=shift.break_minutes,
            exclude_shift_id=giving_up.pk if giving_up is not None else None,
        )
        found.extend({**issue, "employee": taker.pk} for issue in issues)
    return found


def _lock_people(*people):
    for person in sorted({p.pk: p for p in people}.values(), key=lambda p: p.pk):
        lock_employee(person)


def _lock_swap(swap_id):
    return ShiftSwap.objects.select_for_update(of=("self",)).select_related(
        "requester__user", "colleague__user"
    ).get(pk=swap_id)


# --- The steps ------------------------------------------------------------------------------

@transaction.atomic
def request_swap(*, requester, offered_shift_id, colleague_id, note=""):
    shifts = _lock_shifts(offered_shift_id)
    offered = shifts.get(offered_shift_id)
    if offered is None or not _usable(offered, requester):
        fail("shift_not_swappable", "You can only offer one of your own published shifts that has not started.")
    colleague = EmployeeProfile.objects.select_related("user").filter(pk=colleague_id).first()
    if colleague is None or not eligible_colleagues(offered).filter(pk=colleague.pk).exists():
        fail("colleague_not_available", "That person does not work at this store on that day.")
    _lock_people(requester, colleague)
    if ShiftSwap.objects.filter(requester=requester, status__in=OPEN_SWAP_STATUSES).count() >= MAX_OPEN_REQUESTS:
        fail("too_many_requests", "You already have several swap requests waiting. Please wait for those first.")
    if _in_other_open_swap(offered):
        fail("already_requested", "This shift is already part of a swap request.")
    if rule_problems(offered, None, requester, colleague):
        not_possible()
    return ShiftSwap.objects.create(
        offered_shift=offered, requester=requester, colleague=colleague, note=note[:300],
    )


@transaction.atomic
def colleague_accepts(*, swap, employee, in_return_shift_id=None):
    swap = _lock_swap(swap.pk)
    if swap.colleague_id != employee.pk or swap.status != SwapStatus.PENDING_COLLEAGUE:
        fail("swap_not_waiting", "This request is not waiting for your answer.")
    shifts = _lock_shifts(swap.offered_shift_id, in_return_shift_id)
    offered = shifts[swap.offered_shift_id]
    if not _usable(offered, swap.requester):
        fail("swap_out_of_date", "This shift has changed since the request was made.")
    requested = None
    if in_return_shift_id is not None:
        requested = shifts.get(in_return_shift_id)
        if (
            requested is None
            or not _usable(requested, employee)
            or requested.rota_week.store_id != offered.rota_week.store_id
        ):
            fail("shift_not_swappable", "Choose one of your own published shifts at the same store.")
        if _in_other_open_swap(requested, swap):
            fail("already_requested", "That shift is already part of a swap request.")
    _lock_people(swap.requester, employee)
    if rule_problems(offered, requested, swap.requester, employee):
        not_possible()
    swap.requested_shift = requested
    swap.status = SwapStatus.PENDING_MANAGER
    swap.colleague_responded_at = timezone.now()
    swap.save()
    return swap


@transaction.atomic
def colleague_declines(*, swap, employee):
    swap = _lock_swap(swap.pk)
    if swap.colleague_id != employee.pk or swap.status != SwapStatus.PENDING_COLLEAGUE:
        fail("swap_not_waiting", "This request is not waiting for your answer.")
    swap.status = SwapStatus.DECLINED
    swap.colleague_responded_at = timezone.now()
    swap.save()
    return swap


@transaction.atomic
def requester_cancels(*, swap, employee):
    swap = _lock_swap(swap.pk)
    if swap.requester_id != employee.pk or swap.status not in OPEN_SWAP_STATUSES:
        fail("swap_not_open", "This request can no longer be cancelled.")
    swap.status = SwapStatus.CANCELLED
    swap.save()
    return swap


@transaction.atomic
def manager_approves(*, swap, by):
    swap = _lock_swap(swap.pk)
    if swap.status != SwapStatus.PENDING_MANAGER:
        fail("swap_not_waiting", "This swap is not waiting for approval.")
    shifts = _lock_shifts(swap.offered_shift_id, swap.requested_shift_id)
    offered = shifts[swap.offered_shift_id]
    requested = shifts.get(swap.requested_shift_id) if swap.requested_shift_id else None
    if not _usable(offered, swap.requester) or (
        requested is not None and not _usable(requested, swap.colleague)
    ):
        fail("swap_out_of_date", "One of the shifts has changed or already started. Reject this request.")
    _lock_people(swap.requester, swap.colleague)
    blocks = rule_problems(offered, requested, swap.requester, swap.colleague)
    if blocks:
        raise_blocks(blocks)

    offered.employee = swap.colleague
    offered.save(update_fields=["employee", "updated_at"])
    if requested is not None:
        requested.employee = swap.requester
        requested.save(update_fields=["employee", "updated_at"])
    swap.status = SwapStatus.APPROVED
    swap.reviewed_by = by
    swap.reviewed_at = timezone.now()
    swap.save()
    return swap


@transaction.atomic
def manager_rejects(*, swap, by, reason=""):
    swap = _lock_swap(swap.pk)
    if swap.status not in OPEN_SWAP_STATUSES:
        fail("swap_not_open", "This swap has already been decided.")
    swap.status = SwapStatus.REJECTED
    swap.decision_note = reason.strip()[:300]
    swap.reviewed_by = by
    swap.reviewed_at = timezone.now()
    swap.save()
    return swap
