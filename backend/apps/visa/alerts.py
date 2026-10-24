from datetime import date

from apps.notifications.api import admin_users, notify, person_name


def term_letter_uploaded(letter):
    notify(
        "term_letter_uploaded", admin_users(), {"employee": person_name(letter.visa.employee)},
        f"letter:{letter.pk}:uploaded",
    )


def term_letter_reviewed(letter):
    kind = "term_letter_verified" if letter.status == "verified" else "term_letter_rejected"
    notify(kind, [letter.visa.employee.user], {"reason": letter.review_note}, f"letter:{letter.pk}:reviewed")


def _when(expiry, today):
    left = (expiry - today).days
    return f"expired {-left} day(s) ago" if left < 0 else f"expires {expiry:%d %b %Y} (in {left} days)"


def send_digest(today, days):
    """One email a day to Owner and HR listing paperwork that needs attention. Nothing to report, no email."""
    from . import letters, services

    expiring = [
        {
            "employee": person_name(v.employee), "number": v.employee.employee_number,
            "type": v.visa_type.name, "when": _when(v.expiry_date, today),
        }
        for v in services.expiring_visas(days, today)
    ]
    missing = [
        {"employee": person_name(v.employee), "number": v.employee.employee_number}
        for v in letters.students_missing_letters(today)
    ]
    if not expiring and not missing:
        return 0
    return notify(
        "visa_digest", admin_users(),
        {"date": today.isoformat(), "expiring": expiring, "missing_letters": missing},
        f"visa_digest:{today.isoformat()}",
    )
