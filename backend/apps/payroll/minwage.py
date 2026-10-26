"""National Minimum Wage / National Living Wage checks.

The right rate depends on the person's age on the day they work, so a rate that was fine at 20 is too low
the day they turn 21. Apprentices (under 19, or in the first year of an apprenticeship) have their own rate.
Without a date of birth the check cannot be done, and approval is blocked rather than assumed to be fine.
"""
from datetime import date

from django.utils import timezone

from . import taxrates


def age_on(born: date, day: date):
    return day.year - born.year - ((day.month, day.day) < (born.month, born.day))


def minimum_rate(born, apprentice_from, day, rates=None):
    """The lowest legal hourly rate on `day`, or None when it cannot be known (no date of birth)."""
    if born is None:
        return None
    rates = rates or taxrates.rates_for(day)
    table = rates["minimum_wage"]
    age = age_on(born, day)
    if apprentice_from and apprentice_from <= day:
        try:
            first_year_ends = apprentice_from.replace(year=apprentice_from.year + 1)
        except ValueError:  # 29 February
            first_year_ends = apprentice_from.replace(year=apprentice_from.year + 1, day=28)
        if age < 19 or day < first_year_ends:
            return table["apprentice"]
    if age >= 21:
        return table["adult"]
    return table["18-20"] if age >= 18 else table["under18"]


def problems_for(slip, born, apprentice_from, rate_for_day):
    """Plain sentences for every paid day whose rate is under the minimum. `rate_for_day(date) -> Decimal|None`."""
    who = slip.employee.employee_number
    dates = [date.fromisoformat(row["date"]) for row in slip.days]
    for mapping in (slip.leave_dates or {}).values():
        dates += [date.fromisoformat(d) for d in mapping]
    if not dates:
        return []
    if born is None:
        return [f"{who} is paid for hours or holiday but has no date of birth, so the minimum wage cannot be checked."]
    out = []
    for day in sorted(set(dates)):
        rate = rate_for_day(day)
        try:
            low = minimum_rate(born, apprentice_from, day)
        except taxrates.UnsupportedTaxYear:
            return [f"{who}: the minimum wage for {day:%d %b %Y} has not been entered yet, so the rate cannot be checked."]
        if rate is not None and low is not None and rate < low:
            out.append(f"{who}: £{rate}/hour on {day:%d %b %Y} is below the minimum wage of £{low}/hour.")
            break  # one line per person is enough to act on
    return out
