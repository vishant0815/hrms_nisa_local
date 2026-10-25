"""Which emails each leave event sends. Sickness is only ever mentioned to the people who run the shop."""
from apps.notifications.api import admin_users, notify, person_name, store_approvers

from apps.accounts.models import Role
from apps.employees.services import stores_for_employee_on
from apps.rota.alerts import shift_info

from .rules import number


def approvers(employee, start, end):
    """The managers of the shops the person works at on those dates. A manager's own leave goes to Owner/HR."""
    if employee.user.role == Role.MANAGER:
        return admin_users()
    people = {}
    for day in {start, end}:
        for store in stores_for_employee_on(employee, day):
            for user in store_approvers(store):
                people[user.pk] = user
    return list(people.values()) or admin_users()


def details(leave):
    return {
        "employee": person_name(leave.employee), "kind": leave.kind,
        "start": leave.start_date.isoformat(), "end": leave.end_date.isoformat(), "half_day": leave.half_day,
        "days": number(leave.days_requested),
        "note": " ".join(leave.note.split()),
    }


def leave_requested(leave):
    notify("leave_requested", approvers(leave.employee, leave.start_date, leave.end_date), details(leave),
           f"leave:{leave.pk}:requested")


def leave_decided(leave):
    ctx = {**details(leave), "status": leave.status, "reason": " ".join(leave.decision_note.split()),
           "days_charged": number(leave.days_charged) if leave.days_charged is not None else None}
    notify("leave_decided", [leave.employee.user], ctx, f"leave:{leave.pk}:decided")


def leave_cancelled_by_employee(leave):
    notify("leave_cancelled_by_employee", approvers(leave.employee, leave.start_date, leave.end_date),
           details(leave), f"leave:{leave.pk}:cancelled_by_employee")


def leave_cancelled_by_manager(leave):
    ctx = {**details(leave), "reason": " ".join(leave.decision_note.split())}
    notify("leave_cancelled_by_manager", [leave.employee.user], ctx, f"leave:{leave.pk}:cancelled_by_manager")


def sick_reported(leave, shifts):
    ctx = {**details(leave), "shifts": [shift_info(s) for s in shifts]}
    notify("sick_reported", approvers(leave.employee, leave.start_date, leave.end_date), ctx,
           f"leave:{leave.pk}:sick")
