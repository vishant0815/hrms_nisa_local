"""Income tax, National Insurance, student loan and pension, worked out the way HMRC's PAYE specification says.

These are suggestions: Owner/HR sees and confirms them on every payslip. Rounding follows the specification:
tax-free pay is rounded up to the penny, taxable pay down to the pound, tax down to the penny.
Anything this code cannot do correctly (Scottish codes, unusual NI letters) returns None with a reason, so a
person fills it in rather than the system guessing.
"""
import re
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_HALF_DOWN, Decimal

PENNY = Decimal("0.01")
POUND = Decimal("1")
ZERO = Decimal("0")

CODE = re.compile(r"^(?:(?P<k>K)(?P<kn>\d{1,5})|(?P<n>\d{1,5})(?P<letter>[LMNTY]))(?P<basis>W1|M1|X)?$")
FIXED = {"BR": Decimal("0.20"), "D0": Decimal("0.40"), "D1": Decimal("0.45")}


class Unsupported(Exception):
    """Something this code deliberately does not guess at."""


def floor_pound(value):
    return Decimal(value).quantize(POUND, rounding=ROUND_DOWN)


def floor_penny(value):
    return Decimal(value).quantize(PENNY, rounding=ROUND_DOWN)


def parse_code(code):
    """-> (kind, number, cumulative). kind is 'std', 'k', 'BR', 'D0', 'D1' or 'NT'."""
    code = (code or "").replace(" ", "").upper()
    if not code:
        raise Unsupported("There is no tax code.")
    if code[0] in "SC":
        raise Unsupported(f"The tax code {code} (Scottish or Welsh) is not supported yet; enter the tax by hand.")
    base, basis = code, ""
    for suffix in ("W1", "M1", "X"):
        if code.endswith(suffix) and code not in FIXED and code != "NT":
            base, basis = code[: -len(suffix)], suffix
    if base in FIXED or base == "NT":
        return base, 0, basis == ""
    found = CODE.match(code)
    if not found:
        raise Unsupported(f"The tax code {code} is not one this system can work with; enter the tax by hand.")
    cumulative = not found.group("basis")
    if found.group("k"):
        return "k", int(found.group("kn")), cumulative
    return "std", int(found.group("n")), cumulative


def code_value(number, per_500):
    """Week-1 tax-free pay (or, for a K code, the extra pay) for a code number."""
    if number == 0:
        return ZERO
    quotient, rest = 0, number
    if number > 500:
        quotient, rest = divmod(number, 500)
        if rest == 0:
            quotient, rest = quotient - 1, 500
    part = ((Decimal(rest) * 10 + 9) / 52).quantize(PENNY, rounding=ROUND_CEILING)
    return part + quotient * per_500


def paye(code, week, pay_now, pay_before, tax_before, rates):
    """Tax due on this payment. May be negative (a refund). pay_before/tax_before are year-to-date before it.

    For a cumulative code the year's tax so far is worked out from all pay to date, then what was already
    taken is subtracted. For a Week 1/Month 1 code each payment stands alone.
    """
    kind, number, cumulative = parse_code(code)
    if kind == "NT":
        return ZERO
    if not cumulative:
        week, pay_before, tax_before = 1, ZERO, ZERO
    pay_to_date = pay_before + pay_now
    if kind in FIXED:
        due = floor_penny(floor_pound(max(pay_to_date, ZERO)) * FIXED[kind])
        return _limit(due - tax_before, pay_now)
    config = rates["tax"]
    value = code_value(number, config["free_pay_per_500"]) * week
    taxable_pay = pay_to_date + value if kind == "k" else pay_to_date - value
    if taxable_pay <= 0:
        due = ZERO
    else:
        due = _band_tax(taxable_pay, week, config["bands"])
    return _limit(due - tax_before, pay_now)


def _band_tax(unrounded, week, bands):
    """Tax on cumulative taxable pay, following the specification's band choice and rounding."""
    taxable = floor_pound(unrounded)
    fraction = Decimal(week) / 52
    four = Decimal("0.0001")
    start_c = start_k = ZERO  # where the chosen band begins, and the tax due at that point (annual)
    full_tax = ZERO
    previous = ZERO
    for limit, rate in bands:
        if limit is not None:
            c_x = (limit * fraction).quantize(four, rounding=ROUND_DOWN)
            if unrounded > c_x.quantize(POUND, rounding=ROUND_CEILING):
                full_tax += (limit - previous) * rate
                previous = limit
                start_c = c_x
                start_k = (full_tax * fraction).quantize(four, rounding=ROUND_DOWN)
                continue
        return floor_penny(start_k + (taxable - start_c) * rate)


def _limit(amount, pay_now):
    """At most half of the pay can be taken as tax in one payment. Refunds are not limited."""
    if amount <= 0:
        return amount
    return min(amount, floor_penny(max(pay_now, ZERO) * Decimal("0.5")))


def employee_ni(category, gross, rates):
    """Employee National Insurance for one week's pay, to the penny (an exact half-penny goes down)."""
    category = (category or "A").upper()
    config = rates["ni"]
    if category == "C":
        return ZERO
    if category not in ("A", "M", "H", "J", "Z"):
        raise Unsupported(f"National Insurance letter {category} is not supported; enter the figure by hand.")
    main = config["main_rate"] if category in ("A", "M", "H") else config["upper_rate"]
    middle = max(min(gross, config["upper_limit"]) - config["primary_threshold"], ZERO)
    above = max(gross - config["upper_limit"], ZERO)
    return (middle * main + above * config["upper_rate"]).quantize(PENNY, rounding=ROUND_HALF_DOWN)


def student_loan_threshold(plan, rates):
    annual, rate = rates["student_loan"][plan]
    return (annual / 52).quantize(POUND, rounding=ROUND_DOWN), rate


def student_loan(plans, gross, rates):
    """Total weekly deduction for every plan the person has, each rounded down to a whole pound."""
    total = ZERO
    for plan in plans:
        threshold, rate = student_loan_threshold(plan, rates)
        total += floor_pound(max(gross - threshold, ZERO) * rate)
    return total


def pension(gross, age, opted_out, rates):
    """Automatic-enrolment contribution for one week. Returns (amount, note)."""
    config = rates["pension"]
    if opted_out:
        return ZERO, "Opted out of the workplace pension."
    if age is None:
        raise Unsupported("There is no date of birth, so pension eligibility cannot be checked.")
    if age < 22 or age >= 66:
        return ZERO, "Not automatically enrolled because of age (22 to State Pension age); they can ask to join."
    if gross <= config["trigger_week"]:
        return ZERO, "Earnings are at or under the weekly pension trigger this week."
    band = max(min(gross, config["upper_week"]) - config["lower_week"], ZERO)
    return (band * config["employee_rate"]).quantize(PENNY, rounding=ROUND_HALF_DOWN), ""
