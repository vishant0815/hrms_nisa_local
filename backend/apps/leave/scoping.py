"""Who can see which leave. Enforced in querysets."""
from apps.accounts.models import Role
from apps.employees.scoping import employees_visible_to
from apps.stores.scoping import is_admin

from .models import LeaveRequest

RELATED = ("employee__user", "reviewed_by")


def leave_visible_to(user):
    """Owner and HR: everyone. A manager: staff at the stores they run, but not other managers
    (a manager's leave is decided by the Owner or HR). Employees use /me/leave/."""
    leave = LeaveRequest.objects.select_related(*RELATED)
    if is_admin(user):
        return leave
    if user.role == Role.MANAGER:
        staff = employees_visible_to(user).exclude(user__role=Role.MANAGER).values_list("pk", flat=True)
        return leave.filter(employee_id__in=staff)
    return leave.none()
