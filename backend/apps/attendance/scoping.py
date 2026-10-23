from apps.accounts.models import Role
from apps.stores.scoping import is_admin, managed_store_ids

from .models import AttendanceRecord


def attendance_visible_to(user):
    """Owner and HR: every store. A manager: only the stores they run. Employees use /me/attendance/."""
    records = AttendanceRecord.objects.select_related("employee__user", "store", "reviewed_by")
    if is_admin(user):
        return records
    if user.role == Role.MANAGER:
        return records.filter(store_id__in=managed_store_ids(user))
    return records.none()
