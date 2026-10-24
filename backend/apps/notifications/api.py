"""How the rest of the system asks for an email. It only writes rows; nothing is sent here."""
from apps.accounts.models import Role, User

from .models import Notification


def notify(kind, recipients, context, key):
    """Queue one email per recipient. `context` is a dict, or a function of the user returning one.

    Safe to call twice with the same `key`: each (key, recipient) pair is queued once.
    """
    rows, seen = [], set()
    for user in recipients:
        if user is None or user.pk in seen:
            continue
        seen.add(user.pk)
        rows.append(Notification(
            recipient=user, kind=kind, dedupe_key=f"{key}:{user.pk}"[:120],
            context=context(user) if callable(context) else context,
        ))
    Notification.objects.bulk_create(rows, ignore_conflicts=True)
    return len(rows)


def admin_users():
    """Owner and HR: the people who handle immigration paperwork."""
    return list(User.objects.filter(role__in=[Role.OWNER, Role.HR], is_active=True).order_by("pk"))


def store_approvers(store):
    """The managers of a store. If it has none, Owner and HR, so a request never goes unseen."""
    managers = list(store.managers.filter(is_active=True).order_by("pk"))
    return managers or admin_users()


def person_name(employee):
    """A display name for emails. Never the email address."""
    return employee.user.get_full_name() or employee.employee_number
