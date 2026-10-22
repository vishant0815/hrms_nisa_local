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


def make_text_pdf(lines):
    """A tiny but valid PDF whose pages hold the given lines of real text."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    def esc(text):
        return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    stream = "BT /F1 11 Tf 40 780 Td 16 TL " + " ".join(f"({esc(line)}) Tj T*" for line in lines) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = "%PDF-1.4\n", []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    return SimpleUploadedFile("letter.pdf", out.encode("latin-1"), content_type="application/pdf")
