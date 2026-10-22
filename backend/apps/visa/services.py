"""Writes to immigration records. Every change is audited."""
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import EmployeeVisa, RightToWorkCheck, VisaAuditLog


def audit(user, action, employee, visa=None, detail=""):
    VisaAuditLog.objects.create(user=user, action=action, employee=employee, visa=visa, detail=detail[:300])


def validate_visa_fields(visa_type, *, start_date, expiry_date, sponsor_reference, manual_work_allowed):
    """Rules that depend on the visa type. Raises a field-keyed ValidationError."""
    errors = {}
    if visa_type.requires_expiry and expiry_date is None:
        errors["expiry_date"] = "An expiry date is required for this visa type."
    if start_date and expiry_date and expiry_date < start_date:
        errors["expiry_date"] = "The expiry date cannot be before the start date."
    if visa_type.requires_sponsorship and not sponsor_reference:
        errors["sponsor_reference"] = "A sponsor reference is required for this visa type."
    if manual_work_allowed is not None and not visa_type.requires_manual_decision:
        errors["manual_work_allowed"] = "This visa type does not need a manual decision."
    if errors:
        raise ValidationError(errors)


@transaction.atomic
def add_visa(*, employee, by, visa_type, **fields):
    """Record a new status and make it current. The previous one stays as history."""
    if not visa_type.is_active:
        raise ValidationError({"visa_type": "This visa type is no longer in use."})
    validate_visa_fields(
        visa_type,
        start_date=fields.get("start_date"),
        expiry_date=fields.get("expiry_date"),
        sponsor_reference=fields.get("sponsor_reference", ""),
        manual_work_allowed=fields.get("manual_work_allowed"),
    )
    EmployeeVisa.objects.filter(employee=employee, is_current=True).update(is_current=False)
    visa = EmployeeVisa.objects.create(
        employee=employee, visa_type=visa_type, is_current=True, created_by=by, **fields
    )
    audit(by, "visa_added", employee, visa, f"type: {visa_type.name}")
    return visa


@transaction.atomic
def update_visa(*, visa, by, **fields):
    for name, value in fields.items():
        setattr(visa, name, value)
    validate_visa_fields(
        visa.visa_type,
        start_date=visa.start_date,
        expiry_date=visa.expiry_date,
        sponsor_reference=visa.sponsor_reference,
        manual_work_allowed=visa.manual_work_allowed,
    )
    visa.save()
    audit(by, "visa_updated", visa.employee, visa, "fields: " + ", ".join(sorted(fields)))
    return visa


@transaction.atomic
def revoke_visa(*, visa, by, reason=""):
    if visa.revoked_on:
        raise ValidationError("This status has already been revoked.")
    visa.revoked_on = timezone.localdate()
    visa.revoked_reason = reason
    visa.save(update_fields=["revoked_on", "revoked_reason", "updated_at"])
    audit(by, "visa_revoked", visa.employee, visa)
    return visa


@transaction.atomic
def record_check(*, employee, by, visa, **fields):
    if visa.employee_id != employee.id:
        raise ValidationError({"visa": "This status belongs to a different employee."})
    if not visa.is_current:
        raise ValidationError({"visa": "Checks can only be recorded for the current status."})
    checked_on = fields["checked_on"]
    if checked_on > timezone.localdate():
        raise ValidationError({"checked_on": "A check cannot be dated in the future."})
    due = fields.get("next_check_due")
    if due is not None and due <= checked_on:
        raise ValidationError({"next_check_due": "The next check must be after this one."})
    check = RightToWorkCheck.objects.create(employee=employee, visa=visa, checked_by=by, **fields)
    audit(by, "rtw_check_recorded", employee, visa, f"outcome: {check.outcome}")
    return check


def expiring_visas(days, today=None):
    """Current, time-limited statuses expiring within `days` (or already expired), earliest first.

    People who have left, and revoked statuses, are left out: nobody needs chasing for them.
    """
    today = today or timezone.localdate()
    return (
        EmployeeVisa.objects.filter(
            is_current=True,
            revoked_on__isnull=True,
            visa_type__requires_expiry=True,
            expiry_date__isnull=False,
            expiry_date__lte=today + timedelta(days=days),
        )
        .filter(Q(employee__end_date__isnull=True) | Q(employee__end_date__gte=today))
        .select_related("visa_type", "employee__user")
        .order_by("expiry_date", "id")
    )
