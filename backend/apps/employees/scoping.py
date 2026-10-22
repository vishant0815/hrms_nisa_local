"""Who can see which employees and cover requests."""
from django.db.models import Prefetch, Q
from django.utils import timezone

from apps.accounts.models import Role
from apps.stores.scoping import is_admin, managed_store_ids

from .models import AssignmentKind, AssignmentStatus, EmployeeProfile, StoreAssignment


def _active_on(prefix: str, on):
    """Q for assignments that are approved and cover the date `on`."""
    return (
        Q(**{f"{prefix}status": AssignmentStatus.APPROVED, f"{prefix}valid_from__lte": on})
        & (Q(**{f"{prefix}valid_to__isnull": True}) | Q(**{f"{prefix}valid_to__gte": on}))
    )


def _current_or_upcoming(prefix: str, on):
    """Q for approved assignments that have not ended by `on` (already started, or still to come).

    Managers need to see staff arriving next week so they can plan the rota.
    """
    return Q(**{f"{prefix}status": AssignmentStatus.APPROVED}) & (
        Q(**{f"{prefix}valid_to__isnull": True}) | Q(**{f"{prefix}valid_to__gte": on})
    )


def current_home_prefetch(on):
    """Prefetch each employee's current home assignment, avoiding one query per employee."""
    return Prefetch(
        "assignments",
        queryset=StoreAssignment.objects.filter(kind=AssignmentKind.HOME)
        .filter(_active_on("", on))
        .select_related("store")
        .order_by("-valid_from"),
        to_attr="current_home",
    )


def employees_visible_to(user):
    today = timezone.localdate()
    queryset = EmployeeProfile.objects.select_related("user")
    if is_admin(user):
        visible = queryset
    elif user.role == Role.MANAGER:
        # Their own profile, plus staff assigned (home or approved cover) to a store they run,
        # including approved assignments that start in the future.
        at_my_store = Q(assignments__store_id__in=managed_store_ids(user)) & _current_or_upcoming(
            "assignments__", today
        )
        visible = queryset.filter(at_my_store | Q(user=user)).distinct()
    else:
        visible = queryset.filter(user=user)
    return visible.prefetch_related(current_home_prefetch(today))


def cover_requests_visible_to(user):
    queryset = StoreAssignment.objects.filter(kind=AssignmentKind.COVER).select_related(
        "employee__user", "store", "home_store"
    )
    if is_admin(user):
        return queryset
    if user.role == Role.MANAGER:
        ids = managed_store_ids(user)
        return queryset.filter(Q(store_id__in=ids) | Q(home_store_id__in=ids))
    return queryset.filter(employee__user=user)
