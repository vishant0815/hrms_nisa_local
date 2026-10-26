"""The password-protected payslip PDF attached to the Thursday email.

The password is the employee's first name in lower case followed by their date of birth as mmyydd
(month, last two digits of the year, day): Alex, born 5 June 1990, gets alex069005. It is worked out
when the email is sent and is never stored or written to a log.

A PDF is only attached when it can be protected. With no date of birth on file nothing is attached
and the email tells the person to sign in instead.
"""
from datetime import date
from io import BytesIO

from pypdf import PdfReader, PdfWriter

from . import crypto
from .models import PayDetails, Payslip


def password_for(user, born: date):
    first = "".join((user.first_name or "").split()).lower()
    if not first or born is None:
        return None
    return f"{first}{born:%m%y%d}"


def date_of_birth(employee):
    details = PayDetails.objects.filter(employee=employee).first()
    text = crypto.decrypt(details.date_of_birth_encrypted) if details else ""
    return date.fromisoformat(text) if text else None


def can_protect(employee):
    return password_for(employee.user, date_of_birth(employee)) is not None


def protect(pdf_bytes: bytes, password: str) -> bytes:
    writer = PdfWriter(clone_from=PdfReader(BytesIO(pdf_bytes)))
    writer.encrypt(user_password=password, owner_password=None, algorithm="AES-256")
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def payslip_pdf(context, user):
    """[(filename, bytes, mimetype)] for a payslip email, or [] when nothing should be attached."""
    if not context.get("attach"):
        return []
    slip = Payslip.objects.select_related("employee__user").get(pk=context["payslip_id"])
    if slip.employee.user_id != user.pk:
        return []
    password = password_for(slip.employee.user, date_of_birth(slip.employee))
    if password is None or not slip.pdf:
        return []
    with slip.pdf.open("rb") as handle:
        raw = handle.read()
    return [(f"payslip-{slip.number}.pdf", protect(raw, password), "application/pdf")]
