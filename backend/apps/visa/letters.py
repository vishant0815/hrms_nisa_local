"""Term-dates letters: students upload them, HR verifies them and enters the dates.

Uploaded files are untrusted. The type is decided by reading the file's first bytes (not its
name or the type the browser claims), the size is capped, and the stored name is random.
There is no virus scanning here; add one (e.g. ClamAV) before going live.
"""
import re
import uuid

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from . import alerts, reading
from .models import EmployeeVisa, LetterStatus, TermLetter, VacationPeriod
from .services import audit

MAX_BYTES = 5 * 1024 * 1024
MAX_PENDING = 3
# content type -> (extension, the bytes every genuine file of that type starts with)
FILE_TYPES = {
    "application/pdf": (".pdf", b"%PDF-"),
    "image/jpeg": (".jpg", b"\xff\xd8\xff"),
    "image/png": (".png", b"\x89PNG\r\n\x1a\n"),
}


def detect_file_type(upload):
    """The real type of an uploaded file, or None if it is not a PDF, JPEG or PNG."""
    upload.seek(0)
    head = upload.read(16)
    upload.seek(0)
    for content_type, (_, signature) in FILE_TYPES.items():
        if head.startswith(signature):
            return content_type
    return None


def clean_filename(name):
    """A harmless name to show back to people. It is never used on disk."""
    name = re.sub(r"[^A-Za-z0-9._ -]", "", str(name).replace("\\", "/").split("/")[-1]).strip(" .")
    return (name or "letter")[:100]


@transaction.atomic
def upload_letter(*, employee, user, upload, institution=""):
    visa = (
        EmployeeVisa.objects.select_for_update()
        .filter(employee=employee, is_current=True, revoked_on__isnull=True)
        .select_related("visa_type")
        .first()
    )
    if visa is None or not visa.visa_type.requires_term_letter:
        raise ValidationError("A term-dates letter is not needed for your current status.")
    if upload.size > MAX_BYTES:
        raise ValidationError({"file": "The file is too large. The limit is 5 MB."})
    if upload.size == 0:
        raise ValidationError({"file": "The file is empty."})
    content_type = detect_file_type(upload)
    if content_type is None:
        raise ValidationError({"file": "Upload a PDF, JPEG or PNG file."})
    if TermLetter.objects.filter(visa=visa, status=LetterStatus.PENDING).count() >= MAX_PENDING:
        raise ValidationError("You already have letters waiting for HR to check. Please wait for those first.")

    original = clean_filename(upload.name)
    upload.name = f"{uuid.uuid4().hex}{FILE_TYPES[content_type][0]}"
    letter = TermLetter.objects.create(
        visa=visa,
        file=upload,
        original_filename=original,
        content_type=content_type,
        size_bytes=upload.size,
        institution=institution[:150],
        uploaded_by=user,
        suggested=reading.suggestions_for(upload, content_type),
    )
    audit(user, "term_letter_uploaded", employee, visa)
    alerts.term_letter_uploaded(letter)
    return letter


def _require_pending(letter):
    if letter.status != LetterStatus.PENDING:
        raise ValidationError("This letter has already been reviewed.")


@transaction.atomic
def verify_letter(*, letter, by, covers_from, covers_to, vacations):
    """HR has read the letter. Record the dates it covers and its vacation periods."""
    letter = TermLetter.objects.select_for_update().select_related("visa__employee").get(pk=letter.pk)
    _require_pending(letter)
    visa = letter.visa
    if not visa.is_current or visa.revoked_on:
        raise ValidationError("This letter belongs to an earlier immigration status, so it cannot be verified.")
    if covers_to < covers_from:
        raise ValidationError({"covers_to": "The end date cannot be before the start date."})
    for vacation in vacations:
        if vacation["end_date"] < vacation["start_date"]:
            raise ValidationError({"vacations": "A vacation cannot end before it starts."})
        if vacation["start_date"] < covers_from or vacation["end_date"] > covers_to:
            raise ValidationError({"vacations": "Every vacation must fall within the dates the letter covers."})

    letter.status = LetterStatus.VERIFIED
    letter.covers_from = covers_from
    letter.covers_to = covers_to
    letter.reviewed_by = by
    letter.reviewed_at = timezone.now()
    letter.save()
    VacationPeriod.objects.bulk_create(
        [VacationPeriod(visa=visa, term_letter=letter, **vacation) for vacation in vacations]
    )
    audit(by, "term_letter_verified", visa.employee, visa)
    alerts.term_letter_reviewed(letter)
    return letter


@transaction.atomic
def reject_letter(*, letter, by, reason):
    letter = TermLetter.objects.select_for_update().select_related("visa__employee").get(pk=letter.pk)
    _require_pending(letter)
    if not reason.strip():
        raise ValidationError({"reason": "Say why, so the student knows what to fix."})
    letter.status = LetterStatus.REJECTED
    letter.review_note = reason.strip()
    letter.reviewed_by = by
    letter.reviewed_at = timezone.now()
    letter.save()
    audit(by, "term_letter_rejected", letter.visa.employee, letter.visa)
    alerts.term_letter_reviewed(letter)
    return letter


def students_missing_letters(today=None):
    """Current statuses that need a term letter but have no verified one covering today."""
    from django.db.models import Q

    today = today or timezone.localdate()
    covered = TermLetter.objects.filter(
        status=LetterStatus.VERIFIED, covers_from__lte=today, covers_to__gte=today
    ).values("visa_id")
    return (
        EmployeeVisa.objects.filter(
            is_current=True, revoked_on__isnull=True, visa_type__requires_term_letter=True
        )
        .exclude(pk__in=covered)
        .filter(Q(employee__end_date__isnull=True) | Q(employee__end_date__gte=today))
        .select_related("visa_type", "employee__user")
        .order_by("employee__employee_number")
    )
