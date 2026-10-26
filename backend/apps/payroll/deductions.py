"""Suggested tax, National Insurance, student loan and pension for a payslip.

Owner/HR still confirms the figures. Whatever cannot be worked out safely is left for a person, with the
reason written on the payslip, so the system never silently guesses.
"""
from decimal import Decimal

from django.conf import settings

from . import attachments, calc, minwage, tax, taxrates
from .models import PayDetails, Payslip, RunStatus

ZERO = Decimal("0")


def taxable_of(slip):
    relief_at_source = settings.PAYROLL_PENSION_TAX_METHOD == "relief_at_source"
    return slip.gross_pay if relief_at_source else slip.gross_pay - slip.pension


def year_so_far(slip, details):
    """(taxable pay, tax paid) in this tax year before this payslip, or None when it cannot be known."""
    run = slip.pay_run
    earlier = Payslip.objects.filter(
        employee=slip.employee, pay_run__status__in=[RunStatus.APPROVED, RunStatus.SENT],
        pay_run__pay_date__gte=calc.tax_year_start(run.pay_date), pay_run__pay_date__lt=run.pay_date,
    ).exclude(pay_run=run)
    pay = sum((taxable_of(p) for p in earlier), ZERO)
    paid = sum((p.income_tax for p in earlier), ZERO)
    have_earlier = earlier.exists()
    if details and details.prior_pay is not None and details.prior_tax is not None:
        return pay + details.prior_pay, paid + details.prior_tax
    if have_earlier or calc.tax_week(run.pay_date) == 1:
        return pay, paid
    return None


def suggest(slip, details=None):
    """-> (values, notes). A value of None means: a person must enter it."""
    details = details if details is not None else PayDetails.objects.filter(employee=slip.employee).first()
    values = {"income_tax": None, "national_insurance": None, "pension": None, "student_loan": None}
    notes = []
    run = slip.pay_run
    try:
        rates = taxrates.rates_for(run.pay_date)
    except taxrates.UnsupportedTaxYear as problem:
        return values, [str(problem)]
    gross = slip.gross_pay
    born = attachments.date_of_birth(slip.employee)
    age = minwage.age_on(born, run.pay_date) if born else None

    try:
        amount, note = tax.pension(gross, age, bool(details and details.pension_opted_out), rates)
        contribution = amount
        if settings.PAYROLL_PENSION_TAX_METHOD == "relief_at_source":
            amount = (amount * Decimal("0.8")).quantize(tax.PENNY)
        values["pension"] = amount
        if note:
            notes.append(note)
    except tax.Unsupported as problem:
        notes.append(f"Pension: {problem}")
        contribution = None

    try:
        letter = (details.ni_category if details else "") or ""
        if not letter:
            if age is None:
                raise tax.Unsupported("There is no date of birth, so the National Insurance letter cannot be chosen.")
            if age >= 66:
                raise tax.Unsupported("This person may be past State Pension age: set the National Insurance letter (C if they are).")
            letter = "M" if age < 21 else "A"
        values["national_insurance"] = tax.employee_ni(letter, gross, rates)
    except tax.Unsupported as problem:
        notes.append(f"National Insurance: {problem}")

    plans = []
    if details and details.student_loan_plan:
        plans.append(details.student_loan_plan)
    if details and details.postgrad_loan:
        plans.append("postgrad")
    values["student_loan"] = tax.student_loan(plans, gross, rates)

    try:
        code = details.tax_code if details else ""
        parsed = tax.parse_code(code)
        pay_now = gross if settings.PAYROLL_PENSION_TAX_METHOD == "relief_at_source" else gross - (values["pension"] or ZERO)
        if contribution is None:
            raise tax.Unsupported("the pension could not be worked out, and tax depends on it.")
        before = year_so_far(slip, details) if parsed[2] else (ZERO, ZERO)
        if before is None:
            raise tax.Unsupported(
                "this is not the first week of the tax year and there is no pay history: enter the pay and tax so far "
                "from the P45, or give the person a Week 1/Month 1 tax code."
            )
        values["income_tax"] = tax.paye(code, calc.tax_week(run.pay_date), pay_now, before[0], before[1], rates)
        if values["income_tax"] < 0:
            notes.append(f"Income tax: £{-values['income_tax']} is due back to this person; nil is taken this week. Check with the accountant.")
            values["income_tax"] = ZERO
    except tax.Unsupported as problem:
        notes.append(f"Income tax: {problem}")
    return values, notes


def apply_suggestion(slip, details=None):
    """Fill in the deductions that can be worked out; the rest keep whatever a person typed."""
    if slip.gross_pay <= 0:
        slip.deduction_notes = []
        slip.save(update_fields=["deduction_notes"])
        return slip
    values, notes = suggest(slip, details)
    for field, value in values.items():
        if value is not None:
            setattr(slip, field, value)
    slip.deduction_notes = notes
    slip.save(update_fields=[*values.keys(), "deduction_notes"])
    return slip
