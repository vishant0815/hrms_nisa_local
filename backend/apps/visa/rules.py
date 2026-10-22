"""Can this person work on this date? The answer the rota and attendance modules rely on.

This returns a decision; it does not block anything itself. Callers choose whether a "no"
stops the action or only warns HR.

Conservative by design: where the rules are unclear the answer is "no" with a reason, never a
silent "yes". Visa rules change, so confirm them against current Home Office guidance; this
is a record-keeping and alerting aid, not legal advice.
"""
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Optional

from .models import EXPIRY_WARNING_DAYS, CheckState, EmployeeVisa


class Code:
    NO_RECORD = "no_visa_record"
    NOT_PERMITTED = "not_permitted"
    REVOKED = "revoked"
    EXPIRED = "expired"
    EXPIRY_MISSING = "expiry_missing"
    NOT_YET_VALID = "not_yet_valid"
    SPONSOR_MISSING = "sponsor_missing"
    MANUAL_DECISION = "manual_decision_needed"
    CHECK_MISSING = "check_missing"
    CHECK_FAILED = "check_failed"
    CHECK_FOLLOW_UP = "check_follow_up"
    CHECK_OVERDUE = "check_overdue"
    HOURS_LIMIT = "hours_limit_exceeded"


@dataclass
class WorkDecision:
    allowed: bool
    reasons: list = field(default_factory=list)      # [(code, text)] why the answer is no
    warnings: list = field(default_factory=list)     # [text] allowed, but HR should know
    weekly_hours_limit: Optional[int] = None         # cap that applies on the date, if any

    @property
    def codes(self) -> set:
        return {code for code, _ in self.reasons}

    def for_hr(self) -> dict:
        """Full detail. Only Owner and HR may see this: it can reveal immigration status."""
        return {
            "allowed": self.allowed,
            "reasons": [{"code": code, "text": text} for code, text in self.reasons],
            "warnings": list(self.warnings),
            "weekly_hours_limit": self.weekly_hours_limit,
        }

    def for_manager(self) -> dict:
        """Yes/no and the hours limit only. No visa type, dates or check details."""
        if self.allowed:
            message = ""
        elif self.codes == {Code.HOURS_LIMIT}:
            message = f"Over the weekly hours limit of {self.weekly_hours_limit}."
        else:
            message = "Not cleared to work. Please contact HR."
        return {
            "allowed": self.allowed,
            "weekly_hours_limit": self.weekly_hours_limit,
            "message": message,
        }


def week_bounds(day):
    """Monday to Sunday, the week used for the Student hours cap."""
    start = day - timedelta(days=day.weekday())
    return start, start + timedelta(days=6)


def current_visa(employee) -> Optional[EmployeeVisa]:
    return (
        EmployeeVisa.objects.filter(employee=employee, is_current=True)
        .select_related("visa_type")
        .first()
    )


def check_right_to_work(employee, on, week_hours: Optional[Decimal] = None) -> WorkDecision:
    """Decide whether `employee` may work on date `on`.

    `week_hours` is the total planned for that Monday-Sunday week across ALL stores, including
    the shift being checked. Leave it out to skip the hours check.
    """
    visa = current_visa(employee)
    if visa is None:
        return WorkDecision(False, [(Code.NO_RECORD, "No immigration or right-to-work record is on file.")])

    visa_type = visa.visa_type
    reasons: list = []
    warnings: list = []

    if visa.revoked_on:
        reasons.append((Code.REVOKED, "This immigration status has been revoked."))
    if not visa_type.can_work:
        reasons.append((Code.NOT_PERMITTED, f"{visa_type.name} does not give the right to work."))

    if visa.start_date and visa.start_date > on:
        reasons.append((Code.NOT_YET_VALID, f"This status starts on {visa.start_date:%d %b %Y}."))
    if visa_type.requires_expiry:
        if visa.expiry_date is None:
            reasons.append((Code.EXPIRY_MISSING, "A time-limited status has no expiry date recorded."))
        elif visa.expiry_date < on:
            reasons.append((Code.EXPIRED, f"This status expires on {visa.expiry_date:%d %b %Y}."))
        elif (visa.expiry_date - on).days <= EXPIRY_WARNING_DAYS:
            warnings.append(f"Status expires on {visa.expiry_date:%d %b %Y}.")

    if visa_type.requires_sponsorship:
        if not visa.sponsor_reference:
            reasons.append((Code.SPONSOR_MISSING, "No sponsor reference is recorded."))
        else:
            warnings.append("Sponsored status: work only in the sponsored role and hours.")

    if visa_type.requires_manual_decision and visa.manual_work_allowed is not True:
        reasons.append((Code.MANUAL_DECISION, "HR has not confirmed this person may work."))

    state = visa.check_state(on)
    if state == CheckState.MISSING:
        reasons.append((Code.CHECK_MISSING, "No right-to-work check has been recorded."))
    elif state == CheckState.FAILED:
        reasons.append((Code.CHECK_FAILED, "The latest right-to-work check failed."))
    elif state == CheckState.FOLLOW_UP:
        reasons.append((Code.CHECK_FOLLOW_UP, "The latest right-to-work check needs a follow-up."))
    elif state == CheckState.OVERDUE:
        reasons.append((Code.CHECK_OVERDUE, "A follow-up right-to-work check is overdue."))

    limit = visa.weekly_hours_cap_override or visa_type.max_weekly_hours
    if limit is not None and visa.vacations.filter(start_date__lte=on, end_date__gte=on).exists():
        warnings.append("Official vacation: the weekly hours cap does not apply.")
        limit = None
    if limit is not None and week_hours is not None and Decimal(week_hours) > limit:
        reasons.append(
            (Code.HOURS_LIMIT, f"{week_hours} hours in the week exceeds the limit of {limit}.")
        )

    return WorkDecision(
        allowed=not reasons, reasons=reasons, warnings=warnings, weekly_hours_limit=limit
    )
