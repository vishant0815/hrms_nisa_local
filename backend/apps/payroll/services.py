import re
from datetime import timedelta
from io import BytesIO

from decimal import Decimal

from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.utils import timezone
from PIL import Image, UnidentifiedImageError
from rest_framework.exceptions import ValidationError

from apps.attendance.models import AttendanceRecord
from apps.employees.models import EmployeeProfile

from . import calc, crypto, pdf, rules
from .models import HoursApproval, PayDetails, PayRate, PayRun, Payslip, PayslipTemplate, RunStatus


def update_template(user, data: dict):
    template = PayslipTemplate.current()
    for field, value in data.items():
        setattr(template, field, value)
    template.updated_by = user
    template.save()
    return template


def check_logo(raw: bytes):
    """Return (content_type, extension) for an acceptable logo, or raise a readable error."""
    if not raw:
        raise ValidationError({"logo": "The file is empty."})
    if len(raw) > rules.MAX_LOGO_BYTES:
        raise ValidationError({"logo": "The logo must be 1 MB or smaller."})
    try:
        with Image.open(BytesIO(raw)) as image:
            image.load()  # decodes fully, so a damaged or disguised file fails here
            kind, size = image.format, image.size
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValidationError({"logo": "That file is not a PNG or JPEG picture."})
    if kind not in rules.LOGO_TYPES:
        raise ValidationError({"logo": "Use a PNG or JPEG picture."})
    if max(size) > rules.MAX_LOGO_SIDE:
        raise ValidationError({"logo": f"The logo can be at most {rules.MAX_LOGO_SIDE} pixels on its longest side."})
    return rules.LOGO_TYPES[kind]


def set_logo(user, raw: bytes):
    content_type, extension = check_logo(raw)
    template = PayslipTemplate.current()
    if template.logo:
        template.logo.delete(save=False)
    template.logo.save(f"logo{extension}", ContentFile(raw), save=False)
    template.updated_by = user
    template.save()
    return template, content_type


def clear_logo(user):
    template = PayslipTemplate.current()
    if template.logo:
        template.logo.delete(save=False)
    template.logo = ""
    template.updated_by = user
    template.save()
    return template


# --- Pay details, rates ------------------------------------------------------------------------

NI_NUMBER = re.compile(r"^[A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z]\d{6}[A-D]$")
TAX_CODE = re.compile(r"^[A-Z0-9]{2,9}$")


def fail(field, message):
    raise ValidationError({field: message})


def add_rate(user, employee, hourly_rate, effective_from):
    if hourly_rate <= 0:
        fail("hourly_rate", "The rate must be more than zero.")
    try:
        with transaction.atomic():
            return PayRate.objects.create(
                employee=employee, hourly_rate=hourly_rate, effective_from=effective_from, set_by=user
            )
    except IntegrityError:
        fail("effective_from", "This person already has a rate starting on that date. Choose another date.")


def set_pay_details(user, employee, data: dict):
    details, _ = PayDetails.objects.get_or_create(employee=employee)
    if "ni_number" in data:
        value = data["ni_number"].replace(" ", "").upper()
        if value and not NI_NUMBER.match(value):
            fail("ni_number", "That is not a valid National Insurance number (for example AB123456C).")
        details.ni_number = value
    if "tax_code" in data:
        value = data["tax_code"].replace(" ", "").upper()
        if value and not TAX_CODE.match(value):
            fail("tax_code", "That is not a valid tax code (for example 1257L).")
        details.tax_code = value
    if "account_name" in data:
        details.account_name = data["account_name"].strip()
    if "sort_code" in data:
        value = re.sub(r"[\s-]", "", data["sort_code"])
        if value and not re.fullmatch(r"\d{6}", value):
            fail("sort_code", "A sort code is six digits, for example 12-34-56.")
        details.sort_code_encrypted = crypto.encrypt(value)
    if "account_number" in data:
        value = re.sub(r"\s", "", data["account_number"])
        if value and not re.fullmatch(r"\d{8}", value):
            fail("account_number", "An account number is eight digits.")
        details.account_number_encrypted = crypto.encrypt(value)
    details.updated_by = user
    details.save()
    return details


def approve_extra_hours(user, record, note=""):
    if record.clock_out_at is None:
        fail("record", "This person is still clocked in.")
    approval, _ = HoursApproval.objects.update_or_create(
        record=record, defaults={"approved_by": user, "note": note.strip()[:300]}
    )
    return approval


# --- Pay runs ----------------------------------------------------------------------------------

def paid_record_ids(exclude_run=None):
    ids = set()
    paid = Payslip.objects.filter(pay_run__status__in=[RunStatus.APPROVED, RunStatus.SENT])
    if exclude_run is not None:
        paid = paid.exclude(pay_run=exclude_run)
    for chunk in paid.values_list("record_ids", flat=True):
        ids.update(chunk)
    return ids


def default_pay_date(period_end):
    return period_end + timedelta(days=4)  # the Thursday after a Sunday


@transaction.atomic
def create_pay_run(user, period_start, pay_date=None, now=None):
    if period_start.weekday() != 0:
        fail("period_start", "A pay week starts on a Monday.")
    if PayRun.objects.filter(period_start=period_start).exists():
        fail("period_start", "There is already a pay run for that week.")
    if PayRun.objects.filter(period_start__lt=period_start, status=RunStatus.DRAFT).exists():
        fail("period_start", "An earlier pay run is still a draft. Approve it first, so no hours are paid twice.")
    period_end = period_start + timedelta(days=6)
    pay_date = pay_date or default_pay_date(period_end)
    if pay_date <= period_end:
        fail("pay_date", "The pay date must be after the end of the pay week.")
    run = PayRun.objects.create(period_start=period_start, period_end=period_end, pay_date=pay_date, created_by=user)
    fill_run(run, now)
    return run


def payslip_number(run, employee):
    return f"{run.pay_date:%Y}-{calc.tax_week(run.pay_date):02d}-{employee.employee_number}"


@transaction.atomic
def fill_run(run, now=None):
    """(Re)build every payslip of a draft from the attendance records."""
    now = now or timezone.now()
    if run.status != RunStatus.DRAFT:
        fail("status", "Only a draft can be rebuilt.")
    first = calc.first_run_start()
    since = min(first, run.period_start) if first else run.period_start
    paid = paid_record_ids(exclude_run=run)
    existing = {p.employee_id: p for p in run.payslips.all()}
    employee_ids = set(
        AttendanceRecord.objects.filter(
            clock_in_at__lt=calc.start_of_day(run.period_end + timedelta(days=1)),
            clock_in_at__gte=calc.start_of_day(since),
        ).exclude(pk__in=paid).values_list("employee_id", flat=True)
    )
    for employee in EmployeeProfile.objects.filter(pk__in=employee_ids).select_related("user"):
        data = calc.build_payslip_data(employee, run.period_end, paid, now, since)
        old = existing.pop(employee.pk, None)
        if data is None:
            if old:
                old.delete()
            continue
        fields = {
            "number": payslip_number(run, employee), "store_id": data["store_id"],
            "paid_minutes": data["paid_minutes"], "unpaid_break_minutes": data["unpaid_break_minutes"],
            "gross_pay": data["gross_pay"], "days": data["days"], "held": data["held"],
            "earnings": data["earnings"], "record_ids": data["record_ids"],
        }
        if old:
            if old.gross_pay != data["gross_pay"]:
                fields["deductions_confirmed"] = False  # tax depends on gross, so it must be checked again
            Payslip.objects.filter(pk=old.pk).update(**fields)
        else:
            Payslip.objects.create(pay_run=run, employee=employee, **fields)
    for leftover in existing.values():
        leftover.delete()
    return run


def set_deductions(user, payslip, data: dict, confirmed):
    if payslip.pay_run.status != RunStatus.DRAFT:
        fail("status", "This payslip has been approved and can no longer be changed.")
    for field in ("income_tax", "national_insurance", "pension", "student_loan", "other_deductions"):
        if field in data:
            if data[field] < 0:
                fail(field, "A deduction cannot be negative.")
            setattr(payslip, field, data[field])
    if payslip.total_deductions > payslip.gross_pay:
        fail("income_tax", "The deductions are more than the gross pay.")
    if confirmed is not None:
        payslip.deductions_confirmed = confirmed
    payslip.deductions_by = user
    payslip.save()
    return payslip


def blockers(run):
    """What stops this run being approved, as plain sentences."""
    problems = []
    for slip in run.payslips.select_related("employee__user"):
        who = slip.employee.employee_number
        if any(item["reason"] == "no_rate" for item in slip.held):
            problems.append(f"{who} has hours but no hourly rate for those days.")
        if slip.paid_minutes > 0 and not slip.deductions_confirmed:
            problems.append(f"{who}: tax, National Insurance and other deductions have not been confirmed.")
        if slip.net_pay < 0:
            problems.append(f"{who}: net pay would be below zero.")
    return problems


@transaction.atomic
def approve_run(user, run, template):
    run = PayRun.objects.select_for_update().get(pk=run.pk)
    if run.status != RunStatus.DRAFT:
        fail("status", "This pay run is already approved.")
    problems = blockers(run)
    clash = paid_record_ids(exclude_run=run) & {i for slip in run.payslips.all() for i in slip.record_ids}
    if clash:
        problems.append("Some hours were already paid in another run. Rebuild this draft first.")
    if problems:
        raise ValidationError({"problems": problems})
    run.status = RunStatus.APPROVED
    run.approved_by, run.approved_at = user, timezone.now()
    run.template_snapshot = snapshot_template(template)
    run.save()
    make_pdfs(run, template)
    return run


def year_to_date(slip):
    """Totals for this person from the start of the tax year up to and including this payslip."""
    run = slip.pay_run
    earlier = Payslip.objects.filter(
        employee=slip.employee, pay_run__status__in=[RunStatus.APPROVED, RunStatus.SENT],
        pay_run__pay_date__gte=calc.tax_year_start(run.pay_date), pay_run__pay_date__lte=run.pay_date,
    )
    totals = {key: Decimal("0") for key in ("gross_pay", "income_tax", "national_insurance", "pension", "student_loan", "other_deductions")}
    for row in earlier:
        for key in totals:
            totals[key] += getattr(row, key)
    totals["total_deductions"] = sum(totals[k] for k in totals if k != "gross_pay")
    totals["net_pay"] = totals["gross_pay"] - totals["total_deductions"]
    totals["tax_week"] = calc.tax_week(run.pay_date)
    totals["tax_year"] = calc.tax_year_label(run.pay_date)
    return totals


def masked_details(employee):
    from .serializers import mask_details

    return mask_details(PayDetails.objects.filter(employee=employee).first())


def make_pdfs(run, template):
    logo = None
    if template.logo:
        try:
            with template.logo.open("rb") as handle:
                logo = handle.read()
        except FileNotFoundError:
            logo = None
    for slip in run.payslips.select_related("employee__user", "store", "pay_run"):
        data = pdf.render(slip, run.template_snapshot, masked_details(slip.employee), year_to_date(slip), logo)
        slip.pdf.save(f"{slip.number}-{slip.pk}.pdf", ContentFile(data), save=True)


def snapshot_template(template):
    return {
        "trading_name": template.trading_name, "legal_employer_name": template.legal_employer_name,
        "employer_address": template.employer_address, "paye_reference": template.paye_reference,
        "company_number": template.company_number, "hr_email": template.hr_email,
        "footer_note": template.footer_note, "accent_colour": template.accent_colour,
        "show_bank_line": template.show_bank_line,
    }


def bank_rows(run):
    """(payments, people missing bank details) for everyone with money to receive."""
    rows, missing = [], []
    reference = f"WAGES {run.pay_date:%d%b}".upper()
    for slip in run.payslips.select_related("employee__user"):
        if slip.net_pay <= 0:
            continue
        details = PayDetails.objects.filter(employee=slip.employee).first()
        sort_code = crypto.decrypt(details.sort_code_encrypted) if details else ""
        account = crypto.decrypt(details.account_number_encrypted) if details else ""
        if not (sort_code and account):
            missing.append(slip.employee.employee_number)
            continue
        name = (details.account_name or slip.employee.user.get_full_name() or slip.employee.employee_number)[:18]
        rows.append({
            "name": name, "sort_code": f"{sort_code[:2]}-{sort_code[2:4]}-{sort_code[4:]}",
            "account_number": account, "amount": f"{slip.net_pay:.2f}", "reference": reference,
        })
    return rows, missing
