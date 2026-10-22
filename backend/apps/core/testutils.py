"""Small helpers shared by the test suites."""
from datetime import timedelta

from django.utils import timezone

from apps.accounts.models import Role, User

STRONG_PASSWORD = "Correct-Horse-9-Battery"


def make_user(email, role=Role.EMPLOYEE, **extra):
    return User.objects.create_user(email=email, password=STRONG_PASSWORD, role=role, **extra)


def days_from_today(days):
    return timezone.localdate() + timedelta(days=days)


def make_pdf(size=200):
    """A minimal file that starts like a real PDF."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    body = b"%PDF-1.4\n" + b"x" * max(0, size - 9)
    return SimpleUploadedFile("term letter.pdf", body, content_type="application/pdf")


def verified_term_letter(visa, by, covers_from, covers_to, vacations=()):
    """Run a student's letter through the real upload and verify flow."""
    from apps.visa import letters

    letter = letters.upload_letter(
        employee=visa.employee, user=visa.employee.user, upload=make_pdf(), institution="Test University"
    )
    return letters.verify_letter(
        letter=letter, by=by, covers_from=covers_from, covers_to=covers_to, vacations=list(vacations)
    )
