"""Leave arithmetic with no database in it, so it is easy to test."""
import math
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings

HALF = Decimal("0.5")


def number(value):
    """Days as people write them: 3, not 3.0, and 2.5 stays 2.5."""
    return format(Decimal(value).normalize(), "f")


def leave_year(day, start_month=None):
    """(first day, last day) of the leave year containing `day`."""
    start_month = start_month or settings.LEAVE_YEAR_START_MONTH
    first = date(day.year if day.month >= start_month else day.year - 1, start_month, 1)
    return first, date(first.year + 1, start_month, 1) - timedelta(days=1)


def days_in_range(start, end, half_day=""):
    """Calendar days asked for. A half day is only possible for a single date."""
    if half_day:
        return HALF
    return Decimal((end - start).days + 1)


def default_allowance(employee, year_start, year_end, full_days=None):
    """The standard allowance, shared out by the days employed in the year and rounded up to a half day."""
    full_days = Decimal(str(full_days if full_days is not None else settings.DEFAULT_ANNUAL_LEAVE_DAYS))
    first = max(year_start, employee.start_date)
    last = min(year_end, employee.end_date) if employee.end_date else year_end
    if last < first:
        return Decimal("0.0")
    employed = (last - first).days + 1
    year_length = (year_end - year_start).days + 1
    share = full_days * employed / year_length
    return Decimal(math.ceil(share * 2)) / 2
