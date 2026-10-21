"""Business rules for employees, home stores and cover requests."""
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.stores.models import Store
from apps.stores.scoping import is_admin, managed_store_ids

from .models import (
    AssignmentKind,
    AssignmentStatus,
    EmployeeProfile,
    StoreAssignment,
)


def _covers(on):
    """Q for approved assignments whose dates include `on`."""
    return (
        Q(status=AssignmentStatus.APPROVED, valid_from__lte=on)
        & (Q(valid_to__isnull=True) | Q(valid_to__gte=on))
    )


# --- Where an employee can work -------------------------------------------------------------

def home_store_on(employee: EmployeeProfile, on):
    assignment = (
        StoreAssignment.objects.filter(employee=employee, kind=AssignmentKind.HOME)
        .filter(_covers(on))
        .select_related("store")
        .order_by("-valid_from")
        .first()
    )
    return assignment.store if assignment else None


def stores_for_employee_on(employee: EmployeeProfile, on):
    """Stores the employee may be rostered at on a date: home store plus approved cover.

    The rota uses this so nobody is scheduled at a shop they are not cleared to work in.
    """
    return Store.objects.filter(
        Q(assignments__employee=employee)
        & Q(assignments__status=AssignmentStatus.APPROVED, assignments__valid_from__lte=on)
        & (Q(assignments__valid_to__isnull=True) | Q(assignments__valid_to__gte=on))
    ).distinct()


# --- Employees and home stores --------------------------------------------------------------

@transaction.atomic
def create_employee(*, user, home_store, start_date, created_by, phone="", job_title=""):
    profile = EmployeeProfile.objects.create(
        user=user, phone=phone, job_title=job_title, start_date=start_date
    )
    StoreAssignment.objects.create(
        employee=profile,
        store=home_store,
        kind=AssignmentKind.HOME,
        status=AssignmentStatus.APPROVED,
        valid_from=start_date,
        requested_by=created_by,
    )
    return profile


@transaction.atomic
def transfer_home_store(*, employee, new_store, effective_date, by):
    """Move an employee's home store from `effective_date`, keeping the history."""
    today = timezone.localdate()
    if effective_date < today:
        raise ValidationError({"effective_date": "The transfer date cannot be in the past."})
    if not new_store.is_active:
        raise ValidationError({"store": "This store is not active."})
    current = (
        StoreAssignment.objects.select_for_update()
        .filter(
            employee=employee, kind=AssignmentKind.HOME,
            status=AssignmentStatus.APPROVED, valid_to__isnull=True,
        )
        .first()
    )
    if current is None:
        raise ValidationError("This employee has no current home store.")
    if current.store_id == new_store.id:
        raise ValidationError({"store": "The employee is already based at this store."})
    if effective_date <= current.valid_from:
        raise ValidationError(
            {"effective_date": "The transfer must start after the current home store began."}
        )
    current.valid_to = effective_date - timedelta(days=1)
    current.save(update_fields=["valid_to", "updated_at"])
    return StoreAssignment.objects.create(
        employee=employee,
        store=new_store,
        kind=AssignmentKind.HOME,
        status=AssignmentStatus.APPROVED,
        valid_from=effective_date,
        requested_by=by,
    )


# --- Cover requests: both managers must approve ---------------------------------------------

def approval_sides(user, home_store_id, store_id):
    """Which sides of a cover request this user can approve: (home side, destination side).

    Owner and HR can approve both sides. A manager can approve the side(s) whose store they run.
    """
    if is_admin(user):
        return True, True
    ids = managed_store_ids(user)
    return home_store_id in ids, store_id in ids


def _record_approvals(assignment, user, home_ok, dest_ok):
    now = timezone.now()
    if home_ok and assignment.home_approved_by_id is None:
        assignment.home_approved_by = user
        assignment.home_approved_at = now
    if dest_ok and assignment.destination_approved_by_id is None:
        assignment.destination_approved_by = user
        assignment.destination_approved_at = now
    if assignment.home_approved_by_id and assignment.destination_approved_by_id:
        assignment.status = AssignmentStatus.APPROVED


@transaction.atomic
def request_cover(*, employee, store, valid_from, valid_to, reason, requested_by):
    today = timezone.localdate()
    if not employee.is_current(today) or not employee.user.is_active:
        raise ValidationError({"employee": "This employee no longer works here."})
    if not store.is_active:
        raise ValidationError({"store": "This store is not active."})
    if valid_to < valid_from:
        raise ValidationError({"valid_to": "The end date cannot be before the start date."})
    if valid_to < today:
        raise ValidationError({"valid_to": "The cover period cannot be entirely in the past."})

    home_store = home_store_on(employee, valid_from)
    if home_store is None:
        raise ValidationError({"employee": "The employee has no home store on the start date."})
    if home_store.id == store.id:
        raise ValidationError({"store": "This is already the employee's home store."})

    home_ok, dest_ok = approval_sides(requested_by, home_store.id, store.id)
    if not (home_ok or dest_ok):
        raise PermissionDenied("You must manage the employee's home store or the destination store.")

    overlapping = StoreAssignment.objects.filter(
        employee=employee,
        store=store,
        kind=AssignmentKind.COVER,
        status__in=[AssignmentStatus.PENDING, AssignmentStatus.APPROVED],
        valid_from__lte=valid_to,
    ).filter(Q(valid_to__isnull=True) | Q(valid_to__gte=valid_from))
    if overlapping.exists():
        raise ValidationError("There is already a cover request for these dates at this store.")

    assignment = StoreAssignment(
        employee=employee,
        store=store,
        home_store=home_store,
        kind=AssignmentKind.COVER,
        status=AssignmentStatus.PENDING,
        valid_from=valid_from,
        valid_to=valid_to,
        reason=reason,
        requested_by=requested_by,
    )
    # The requester's own side counts as approved when they ask.
    _record_approvals(assignment, requested_by, home_ok, dest_ok)
    assignment.save()
    return assignment


def _lock_pending(assignment_id):
    assignment = StoreAssignment.objects.select_for_update().get(
        pk=assignment_id, kind=AssignmentKind.COVER
    )
    if assignment.status != AssignmentStatus.PENDING:
        raise ValidationError("Only pending requests can be changed this way.")
    return assignment


@transaction.atomic
def approve_cover(assignment_id, user):
    assignment = _lock_pending(assignment_id)
    home_ok, dest_ok = approval_sides(user, assignment.home_store_id, assignment.store_id)
    if not (home_ok or dest_ok):
        raise PermissionDenied("You do not manage either store in this request.")
    already = (not home_ok or assignment.home_approved_by_id) and (
        not dest_ok or assignment.destination_approved_by_id
    )
    if already:
        raise ValidationError("Your approval has already been recorded.")
    _record_approvals(assignment, user, home_ok, dest_ok)
    assignment.save()
    return assignment


@transaction.atomic
def reject_cover(assignment_id, user, reason=""):
    assignment = _lock_pending(assignment_id)
    home_ok, dest_ok = approval_sides(user, assignment.home_store_id, assignment.store_id)
    if not (home_ok or dest_ok):
        raise PermissionDenied("You do not manage either store in this request.")
    assignment.status = AssignmentStatus.REJECTED
    assignment.rejected_by = user
    assignment.rejection_reason = reason
    assignment.save()
    return assignment


@transaction.atomic
def cancel_cover(assignment_id, user):
    assignment = StoreAssignment.objects.select_for_update().get(
        pk=assignment_id, kind=AssignmentKind.COVER
    )
    if assignment.status not in (AssignmentStatus.PENDING, AssignmentStatus.APPROVED):
        raise ValidationError("This request can no longer be cancelled.")
    if assignment.valid_to < timezone.localdate():
        raise ValidationError("This cover period has already finished.")
    if not (is_admin(user) or assignment.requested_by_id == user.id):
        raise PermissionDenied("Only the person who made the request, or HR/the owner, can cancel it.")
    assignment.status = AssignmentStatus.CANCELLED
    assignment.save()
    return assignment
