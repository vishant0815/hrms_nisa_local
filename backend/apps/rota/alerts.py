"""Emails for rota and swap events. These only queue rows (see apps.notifications); nothing is sent here."""
from collections import defaultdict

from apps.notifications.api import notify, person_name, store_approvers

from .models import ShiftStatus


def shift_info(shift):
    return {
        "date": shift.date.isoformat(), "start": f"{shift.start_time:%H:%M}", "end": f"{shift.end_time:%H:%M}",
        "store": shift.rota_week.store.name, "role": shift.role,
    }


def rota_published(rota_week, today):
    """Tell each person their own upcoming shifts. People with no shifts get nothing."""
    shifts = (
        rota_week.shifts.filter(status=ShiftStatus.SCHEDULED, employee__isnull=False, date__gte=today)
        .select_related("employee__user", "rota_week__store")
        .order_by("date", "start_time", "id")
    )
    by_person = defaultdict(list)
    for shift in shifts:
        by_person[shift.employee].append(shift_info(shift))
    for employee, items in by_person.items():
        notify(
            "rota_published", [employee.user],
            {"store": rota_week.store.name, "week_start": rota_week.week_start.isoformat(), "shifts": items},
            f"rota_published:{rota_week.pk}",
        )


# --- Swaps ----------------------------------------------------------------------------------

def swap_requested(swap):
    notify(
        "swap_requested", [swap.colleague.user],
        {"requester": person_name(swap.requester), "offered": shift_info(swap.offered_shift)},
        f"swap:{swap.pk}:requested",
    )


def swap_accepted(swap):
    offered = shift_info(swap.offered_shift)
    in_return = shift_info(swap.requested_shift) if swap.requested_shift_id else None
    notify(
        "swap_accepted_manager", store_approvers(swap.offered_shift.rota_week.store),
        {
            "store": swap.offered_shift.rota_week.store.name, "requester": person_name(swap.requester),
            "colleague": person_name(swap.colleague), "offered": offered, "in_return": in_return,
        },
        f"swap:{swap.pk}:accepted_manager",
    )
    notify(
        "swap_accepted_requester", [swap.requester.user],
        {"colleague": person_name(swap.colleague), "offered": offered}, f"swap:{swap.pk}:accepted_requester",
    )


def swap_declined(swap):
    notify(
        "swap_declined", [swap.requester.user],
        {"colleague": person_name(swap.colleague), "offered": shift_info(swap.offered_shift)},
        f"swap:{swap.pk}:declined",
    )


def swap_cancelled(swap):
    notify(
        "swap_cancelled", [swap.colleague.user],
        {"requester": person_name(swap.requester), "offered": shift_info(swap.offered_shift)},
        f"swap:{swap.pk}:cancelled",
    )


def swap_approved(swap):
    offered = shift_info(swap.offered_shift)
    returned = shift_info(swap.requested_shift) if swap.requested_shift_id else None
    notify("swap_approved", [swap.requester.user], {"gets": returned, "gives": offered}, f"swap:{swap.pk}:approved")
    notify("swap_approved", [swap.colleague.user], {"gets": offered, "gives": returned}, f"swap:{swap.pk}:approved")


def swap_rejected(swap):
    notify(
        "swap_rejected", [swap.requester.user, swap.colleague.user],
        {"offered": shift_info(swap.offered_shift), "reason": swap.decision_note},
        f"swap:{swap.pk}:rejected",
    )
