"""The emails themselves: plain text only, so nothing a person types can become markup or a link.

Subjects never contain anything a person typed. Emails to managers and colleagues say what they
need to act on and nothing about immigration status; only Owner/HR emails mention visas.
"""
from datetime import date

from django.conf import settings


def clean_subject(subject):
    return " ".join(str(subject).split())[:150]


def hello(user):
    return f"Hi {user.first_name}," if user.first_name else "Hi,"


def link(path):
    return f"{settings.SITE_URL.rstrip('/')}{path}"


def day(value):
    return date.fromisoformat(value).strftime("%a %d %b")


def shift_line(shift):
    where = f" at {shift['store']}" if shift.get("store") else ""
    role = f" ({shift['role']})" if shift.get("role") else ""
    return f"{day(shift['date'])}, {shift['start']}-{shift['end']}{where}{role}"


def compose(user, lines, path=None, action=None):
    parts = [hello(user), "", *lines]
    if path:
        parts += ["", f"{action or 'Open it here'}: {link(path)}"]
    parts += ["", "HRMS"]
    return "\n".join(parts)


# --- Rota -----------------------------------------------------------------------------------

def rota_published(ctx, user):
    shifts = [f"  - {shift_line(s)}" for s in ctx["shifts"]]
    lines = [f"The rota for {ctx['store']}, week of {day(ctx['week_start'])}, has been published.", "",
             "Your shifts:", *shifts]
    return "Your rota is published", compose(user, lines, "/rota", "See your rota")


# --- Shift swaps ----------------------------------------------------------------------------

def swap_requested(ctx, user):
    lines = [f"{ctx['requester']} would like you to take this shift:", f"  {shift_line(ctx['offered'])}", "",
             "You can accept (and offer one of your own shifts in return) or decline. "
             "A manager has to approve it before anything changes."]
    return "A colleague has asked you to take a shift", compose(user, lines, "/swaps", "Answer the request")


def swap_accepted_requester(ctx, user):
    lines = [f"{ctx['colleague']} has agreed to the swap for {day(ctx['offered']['date'])}.",
             "It now waits for a manager's approval. Your rota will not change until then."]
    return "Your swap is waiting for a manager", compose(user, lines, "/swaps", "See your swaps")


def swap_accepted_manager(ctx, user):
    lines = [f"A swap at {ctx['store']} needs your decision.", "",
             f"  {ctx['requester']} gives: {shift_line(ctx['offered'])}"]
    if ctx.get("in_return"):
        lines.append(f"  {ctx['colleague']} gives: {shift_line(ctx['in_return'])}")
    else:
        lines.append(f"  {ctx['colleague']} takes the shift and gives nothing back.")
    lines += ["", "Approving it re-checks every rota rule before anything changes."]
    return "A shift swap needs your approval", compose(user, lines, "/swaps/review", "Review it")


def swap_declined(ctx, user):
    lines = [f"{ctx['colleague']} has declined your request to take {shift_line(ctx['offered'])}.",
             "Your shift has not changed."]
    return "Your swap request was declined", compose(user, lines, "/swaps", "See your swaps")


def swap_approved(ctx, user):
    lines = ["Your shift swap has been approved."]
    if ctx.get("gets"):
        lines.append(f"  You now work: {shift_line(ctx['gets'])}")
    if ctx.get("gives"):
        lines.append(f"  You no longer work: {shift_line(ctx['gives'])}")
    return "Your shift swap was approved", compose(user, lines, "/rota", "See your rota")


def swap_rejected(ctx, user):
    lines = ["Your swap was not approved, so nobody's shifts have changed.", f"  {shift_line(ctx['offered'])}"]
    if ctx.get("reason"):
        lines += ["", f"Reason from the manager: {ctx['reason']}"]
    return "Your shift swap was not approved", compose(user, lines, "/swaps", "See your swaps")


def swap_cancelled(ctx, user):
    lines = [f"{ctx['requester']} has cancelled the request for you to take {shift_line(ctx['offered'])}.",
             "Nothing has changed on your rota."]
    return "A swap request was cancelled", compose(user, lines)


# --- Attendance -----------------------------------------------------------------------------

def attendance_review(ctx, user):
    lines = [f"{ctx['employee']} at {ctx['store']} ({ctx['when']}) has an entry that needs a look:", "",
             *[f"  - {problem}" for problem in ctx["problems"]], "",
             "It has been saved. Please approve it or reject it with a reason."]
    return "A clock entry needs your review", compose(user, lines, "/attendance/review", "Review it")


# --- Term letters (students) ----------------------------------------------------------------

def term_letter_uploaded(ctx, user):
    lines = [f"{ctx['employee']} has uploaded a term-dates letter. It needs checking."]
    return "A term letter is waiting for you", compose(user, lines, "/hr/term-letters", "Check it")


def term_letter_verified(ctx, user):
    lines = ["HR has checked your term-dates letter and recorded your term and vacation dates.",
             "You do not need to send it again until it runs out."]
    return "Your term letter was accepted", compose(user, lines)


def term_letter_rejected(ctx, user):
    lines = ["HR could not accept your term-dates letter.", f"Reason: {ctx['reason']}", "",
             "Please upload a new one."]
    return "Your term letter was not accepted", compose(user, lines, "/me/term-letters", "Upload a new letter")


# --- Immigration paperwork (Owner and HR only) ----------------------------------------------

def visa_digest(ctx, user):
    lines = [f"Immigration paperwork needing attention on {day(ctx['date'])}:", ""]
    if ctx["expiring"]:
        lines.append("Statuses expiring soon or already expired:")
        lines += [f"  - {r['employee']} ({r['number']}): {r['type']}, {r['when']}" for r in ctx["expiring"]]
        lines.append("")
    if ctx["missing_letters"]:
        lines.append("Students with no valid term-dates letter:")
        lines += [f"  - {r['employee']} ({r['number']})" for r in ctx["missing_letters"]]
        lines.append("")
    return "Immigration paperwork needs attention", compose(user, lines[:-1], "/hr/visas", "Open the visa pages")


RENDERERS = {
    "rota_published": rota_published,
    "swap_requested": swap_requested,
    "swap_accepted_requester": swap_accepted_requester,
    "swap_accepted_manager": swap_accepted_manager,
    "swap_declined": swap_declined,
    "swap_approved": swap_approved,
    "swap_rejected": swap_rejected,
    "swap_cancelled": swap_cancelled,
    "attendance_review": attendance_review,
    "term_letter_uploaded": term_letter_uploaded,
    "term_letter_verified": term_letter_verified,
    "term_letter_rejected": term_letter_rejected,
    "visa_digest": visa_digest,
}
