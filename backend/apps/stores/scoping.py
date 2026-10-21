"""Who can see which stores. Enforced in querysets so the API never leaks other shops' data."""
from apps.accounts.models import Role

from .models import Store

ADMIN_ROLES = (Role.OWNER, Role.HR)


def is_admin(user) -> bool:
    """Owner and HR see every store."""
    return bool(user and user.is_authenticated and user.role in ADMIN_ROLES)


def managed_store_ids(user) -> set[int]:
    """The stores a manager runs. Empty for everyone else."""
    if not (user and user.is_authenticated and user.role == Role.MANAGER):
        return set()
    return set(user.managed_stores.values_list("id", flat=True))


def stores_visible_to(user):
    # Imported here because the employees app depends on this module.
    from apps.employees.models import AssignmentStatus

    if is_admin(user):
        return Store.objects.all()
    if user.role == Role.MANAGER:
        return Store.objects.filter(managers=user)
    # Employees see only the stores they are assigned to.
    return Store.objects.filter(
        assignments__employee__user=user,
        assignments__status=AssignmentStatus.APPROVED,
    ).distinct()
