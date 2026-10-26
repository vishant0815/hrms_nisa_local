"""Who can see which devices and punches. Enforced in querysets."""
from apps.accounts.models import Role
from apps.stores.scoping import is_admin, managed_store_ids

from .models import ClockDevice, Punch


def devices_visible_to(user):
    devices = ClockDevice.objects.select_related("store")
    if is_admin(user):
        return devices
    if user.role == Role.MANAGER:
        return devices.filter(store_id__in=managed_store_ids(user))
    return devices.none()


def punches_visible_to(user):
    """Punches made on devices at the stores a manager runs. Owner and HR: all."""
    punches = Punch.objects.select_related("device__store", "employee__user")
    if is_admin(user):
        return punches
    if user.role == Role.MANAGER:
        return punches.filter(device__store_id__in=managed_store_ids(user))
    return punches.none()
