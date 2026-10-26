from datetime import date, timedelta
from decimal import Decimal
from io import StringIO

from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from apps.notifications.models import Notification
from apps.notifications.test_alerts import inbox, queued, send_everything
from apps.payroll import alerts, services
from apps.payroll.models import PayRun, Payslip, PayslipTemplate, RunStatus
from apps.payroll.test_payruns import MON, NEXT, PayBase

MONDAY_AFTER = MON + timedelta(days=7)      # 2026-10-26: when the draft is built
THURSDAY = MON + timedelta(days=10)         # 2026-10-29: the pay date


class WeekTests(SimpleTestCase):
    def test_the_week_to_pay_is_the_last_complete_monday_to_sunday(self):
        for offset in range(0, 7):  # any day of the week after, Monday to Sunday
            self.assertEqual(alerts.last_pay_week(MONDAY_AFTER + timedelta(days=offset)), MON)
        self.assertEqual(alerts.last_pay_week(MON), MON - timedelta(days=7))


@override_settings(NOTIFICATIONS_BUS="memory", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", SITE_URL="https://hr.example")
class ScheduleTests(PayBase):
    def setUp(self):
        super().setUp()
        self.sample_week()
        mail.outbox = []

    def approve(self, run):
        for slip in run.payslips.all():
            services.set_deductions(self.hr, slip, {"income_tax": Decimal("44.80")}, True)
        services.approve_run(self.hr, run, PayslipTemplate.current())

    def test_monday_builds_the_draft_and_owner_and_hr_are_told(self):
        run = alerts.create_weekly_run(MONDAY_AFTER)
        self.assertEqual((run.period_start, run.status, run.payslips.count()), (MON, RunStatus.DRAFT, 1))
        self.assertEqual(queued("payroll_draft_ready"), sorted([self.owner.email, self.hr.email]))
        send_everything()
        text = inbox(self.hr.email)[0].body
        self.assertIn("Payslips: 1", text)
        self.assertIn("Gross pay: £466.00", text)
        self.assertIn("https://hr.example/payroll/", text)
        self.assertEqual(inbox(self.worker.user.email), [])

    def test_running_monday_again_builds_nothing_new_and_sends_nothing_new(self):
        alerts.create_weekly_run(MONDAY_AFTER)
        self.assertIsNone(alerts.create_weekly_run(MONDAY_AFTER + timedelta(days=2)))
        self.assertEqual(PayRun.objects.count(), 1)
        self.assertEqual(Notification.objects.filter(kind="payroll_draft_ready").count(), 2)

    def test_thursday_emails_each_employee_their_own_summary_once(self):
        run = alerts.create_weekly_run(MONDAY_AFTER)
        self.approve(run)
        self.assertEqual(alerts.send_due_payslips(THURSDAY), 1)
        self.assertEqual(alerts.send_due_payslips(THURSDAY), 0)
        send_everything()
        sent = inbox(self.worker.user.email)
        self.assertEqual(len(sent), 1)
        body = sent[0].body
        for expected in ("Mon 19 Oct to Sun 25 Oct", "Thu 29 Oct", "Hours paid: 38.83", "Net pay: £403.28", "https://hr.example/payslips"):
            self.assertIn(expected, body)
        self.assertEqual(sent[0].attachments, [])
        self.assertNotIn("AB", body.replace("ABOUT", ""))
        self.assertEqual(inbox(self.emp_b.user.email), [])
        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.SENT)
        self.assertIsNotNone(Payslip.objects.get().emailed_at)

    def test_a_payslip_already_marked_as_emailed_is_not_queued_again(self):
        run = alerts.create_weekly_run(MONDAY_AFTER)
        self.approve(run)
        Payslip.objects.update(emailed_at=run.created_at)
        self.assertEqual(alerts.send_due_payslips(THURSDAY), 0)
        self.assertEqual(queued("payslip_ready"), [])

    def test_nothing_goes_out_before_the_pay_date(self):
        run = alerts.create_weekly_run(MONDAY_AFTER)
        self.approve(run)
        self.assertEqual(alerts.send_due_payslips(THURSDAY - timedelta(days=1)), 0)
        self.assertEqual(PayRun.objects.get().status, RunStatus.APPROVED)

    def test_an_unapproved_run_sends_nothing_and_owner_and_hr_are_told(self):
        alerts.create_weekly_run(MONDAY_AFTER)
        self.assertEqual(alerts.send_due_payslips(THURSDAY), 0)
        self.assertEqual(queued("payslip_ready"), [])
        self.assertEqual(queued("payroll_not_approved"), sorted([self.owner.email, self.hr.email]))
        send_everything()
        self.assertIn("no payslips were sent", inbox(self.owner.email)[-1].body)
        self.assertEqual(PayRun.objects.get().status, RunStatus.DRAFT)

    def test_a_run_approved_late_goes_out_on_the_next_run_of_the_job(self):
        run = alerts.create_weekly_run(MONDAY_AFTER)
        alerts.send_due_payslips(THURSDAY)
        self.approve(run)
        self.assertEqual(alerts.send_due_payslips(THURSDAY + timedelta(days=1)), 1)

    def test_a_leaver_with_a_disabled_account_is_not_emailed_but_is_not_retried_either(self):
        run = alerts.create_weekly_run(MONDAY_AFTER)
        self.approve(run)
        self.worker.user.is_active = False
        self.worker.user.save()
        self.assertEqual(alerts.send_due_payslips(THURSDAY), 0)
        self.assertIsNotNone(Payslip.objects.get().emailed_at)

    def test_a_still_unapproved_earlier_run_stops_the_next_draft_and_says_why(self):
        alerts.create_weekly_run(MONDAY_AFTER)
        from unittest import mock

        with mock.patch("apps.payroll.alerts.timezone.localdate", return_value=NEXT + timedelta(days=7)):
            with self.assertRaises(CommandError) as caught:
                call_command("create_weekly_pay_run", stdout=StringIO())
        self.assertIn("still a draft", str(caught.exception))


@override_settings(NOTIFICATIONS_BUS="memory", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class CommandTests(PayBase):
    def test_the_two_commands_report_what_they_did(self):
        self.rec(0, "09:00", "17:00")
        out = StringIO()
        from unittest import mock

        with mock.patch("apps.payroll.alerts.timezone.localdate", return_value=MONDAY_AFTER):
            call_command("create_weekly_pay_run", stdout=out)
            call_command("create_weekly_pay_run", stdout=out)
        self.assertIn("1 payslip(s)", out.getvalue())
        self.assertIn("already exists", out.getvalue())
        out = StringIO()
        with mock.patch("apps.payroll.alerts.timezone.localdate", return_value=THURSDAY):
            call_command("send_payslips", stdout=out)
        self.assertIn("No payslips to send", out.getvalue())
