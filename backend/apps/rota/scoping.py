"""Who can see and change which rotas. Enforced in querysets."""
from apps.accounts.models import Role
from apps.stores.scoping import is_admin, managed_store_ids

from .models import RotaWeek, Shift, ShiftTemplate


def rota_weeks_visible_to(user):
    """Owner and HR: every store. A manager: only the stores they run. Employees use /me/rota/."""
    weeks = RotaWeek.objects.select_related("store")
    if is_admin(user):
        return weeks
    if user.role == Role.MANAGER:
        return weeks.filter(store_id__in=managed_store_ids(user))
    return weeks.none()


def shifts_visible_to(user):
    shifts = Shift.objects.select_related("rota_week__store", "employee__user")
    if is_admin(user):
        return shifts
    if user.role == Role.MANAGER:
        return shifts.filter(rota_week__store_id__in=managed_store_ids(user))
    return shifts.none()


def templates_visible_to(user):
    templates = ShiftTemplate.objects.select_related("store")
    if is_admin(user):
        return templates
    if user.role == Role.MANAGER:
        return templates.filter(store_id__in=managed_store_ids(user))
    return templates.none()
