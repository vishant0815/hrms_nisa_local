"""The payroll emails, and the two jobs that run each week.

  Monday    create_weekly_pay_run: builds the draft for last Monday to Sunday and tells Owner and HR.
  Thursday  send_payslips: emails every approved payslip whose pay date has come. A run that has not
            been approved by its pay date sends nothing and tells Owner and HR instead.

Both can be run as often as you like: each email is queued once.
"""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from apps.notifications.api import admin_users, notify

from . import attachments, services
from .models import PayRun, RunStatus


def last_pay_week(today):
    """The Monday of the last complete Monday-to-Sunday week."""
    return today - timedelta(days=today.weekday() + 7)


def run_summary(run):
    slips = list(run.payslips.all())
    held = sum(len(s.held) for s in slips)
    return {
        "period_start": run.period_start.isoformat(), "period_end": run.period_end.isoformat(),
        "pay_date": run.pay_date.isoformat(), "payslips": len(slips), "held": held,
        "gross": f"{sum((s.gross_pay for s in slips), Decimal('0')):.2f}", "run_id": run.pk,
    }


def create_weekly_run(today=None):
    """Build last week's draft if there is not one yet. Returns the run, or None."""
    today = today or timezone.localdate()
    start = last_pay_week(today)
    if PayRun.objects.filter(period_start=start).exists():
        return None
    run = services.create_pay_run(None, start)
    notify("payroll_draft_ready", admin_users(), run_summary(run), f"payroll:{run.pk}:draft")
    return run


def send_due_payslips(today=None):
    """Email approved payslips whose pay date is today or earlier. Returns how many were queued."""
    today = today or timezone.localdate()
    queued = 0
    for run in PayRun.objects.filter(pay_date__lte=today, status__in=[RunStatus.DRAFT, RunStatus.APPROVED]):
        if run.status == RunStatus.DRAFT:
            notify("payroll_not_approved", admin_users(), run_summary(run), f"payroll:{run.pk}:not-approved:{today}")
            continue
        for slip in run.payslips.select_related("employee__user"):
            if slip.emailed_at is None and slip.employee.user.is_active:
                queued += notify("payslip_ready", [slip.employee.user], {
                    "period_start": run.period_start.isoformat(), "period_end": run.period_end.isoformat(),
                    "pay_date": run.pay_date.isoformat(), "hours": f"{Decimal(slip.paid_minutes) / 60:.2f}",
                    "net_pay": f"{slip.net_pay:.2f}", "payslip_id": slip.pk,
                    "attach": bool(slip.pdf) and attachments.can_protect(slip.employee),
                }, f"payslip:{slip.pk}")
                slip.emailed_at = timezone.now()
                slip.save(update_fields=["emailed_at"])
            elif slip.emailed_at is None:
                slip.emailed_at = timezone.now()  # a leaver has no mailbox to tell; the payslip stays in their account
                slip.save(update_fields=["emailed_at"])
        run.status = RunStatus.SENT
        run.save(update_fields=["status"])
    return queued
