"""The yearly figures. Each tax year is one entry; a year that is not listed here is refused, never guessed.

To add a year: copy the newest entry, change the numbers from the HMRC "rates and thresholds" page and the
PAYE specification, and have the accountant check them. Money is held as strings so it is exact.
"""
from datetime import date
from decimal import Decimal as D

# 2026/27 (6 April 2026 to 5 April 2027). England, Wales and Northern Ireland income tax.
# Figures were taken from payroll publishers quoting HMRC; the accountant must confirm them before first use.
RATES = {
    "2026/27": {
        "tax": {
            "bands": [(D("37700"), D("0.20")), (D("125140"), D("0.40")), (None, D("0.45"))],
            "free_pay_per_500": D("96.16"),
            "basic": D("0.20"), "higher": D("0.40"), "additional": D("0.45"),
        },
        "ni": {  # weekly, employee
            "primary_threshold": D("242"), "upper_limit": D("967"),
            "main_rate": D("0.08"), "upper_rate": D("0.02"),
        },
        "student_loan": {  # annual threshold, rate. Weekly threshold = annual / 52, rounded down to a whole pound.
            "1": (D("26900"), D("0.09")), "2": (D("29385"), D("0.09")),
            "4": (D("33795"), D("0.09")), "5": (D("25000"), D("0.09")),
            "postgrad": (D("21000"), D("0.06")),
        },
        "minimum_wage": {"adult": D("12.71"), "18-20": D("10.85"), "under18": D("8.00"), "apprentice": D("8.00")},
        "pension": {
            "trigger_week": D("192"), "lower_week": D("120"), "upper_week": D("967"),
            "employee_rate": D("0.05"), "employer_rate": D("0.03"),
        },
        "ssp": {"weekly": D("123.25"), "earnings_share": D("0.80")},
    },
}

TAX_YEAR_STARTS = {"2026/27": date(2026, 4, 6)}


class UnsupportedTaxYear(Exception):
    pass


def label_for(on):
    start = on.replace(month=4, day=6)
    if on < start:
        start = start.replace(year=start.year - 1)
    return f"{start.year}/{(start.year + 1) % 100:02d}"


def rates_for(on):
    label = label_for(on)
    if label not in RATES:
        raise UnsupportedTaxYear(
            f"The tax figures for {label} have not been entered yet. Add them in payroll/taxrates.py "
            "(your accountant can confirm them) before running pay for this year."
        )
    return RATES[label]
