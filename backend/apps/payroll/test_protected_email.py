from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO

from django.core import mail
from django.test import SimpleTestCase, override_settings
from pypdf import PdfReader

from apps.notifications.models import Notification
from apps.notifications.test_alerts import inbox, send_everything
from apps.payroll import alerts, attachments, services
from apps.payroll.models import PayslipTemplate
from apps.payroll.test_payruns import MON, PayBase
from apps.payroll.test_schedule import MONDAY_AFTER, THURSDAY


class User:
    def __init__(self, first):
        self.first_name = first


class PasswordTests(SimpleTestCase):
    def test_the_password_is_first_name_then_month_year_day(self):
        self.assertEqual(attachments.password_for(User("Sam"), date(2000, 3, 3)), "sam030003")
        self.assertEqual(attachments.password_for(User("Ann"), date(1998, 11, 25)), "ann119825")
        self.assertEqual(attachments.password_for(User("Priya"), date(1987, 1, 9)), "priya018709")

    def test_names_are_lower_cased_and_spaces_dropped(self):
        self.assertEqual(attachments.password_for(User("  Mary Ann "), date(2001, 12, 31)), "maryann120131")
        self.assertEqual(attachments.password_for(User("ÉLODIE"), date(2001, 12, 31)), "élodie120131")

    def test_no_first_name_or_no_birthday_means_no_password(self):
        self.assertIsNone(attachments.password_for(User(""), date(2000, 1, 1)))
        self.assertIsNone(attachments.password_for(User("   "), date(2000, 1, 1)))
        self.assertIsNone(attachments.password_for(User("Sam"), None))


@override_settings(NOTIFICATIONS_BUS="memory", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", SITE_URL="https://hr.example")
class ProtectedEmailTests(PayBase):
    def setUp(self):
        super().setUp()
        self.sample_week()
        self.worker.user.first_name = "Sam"
        self.worker.user.save()
        mail.outbox = []

    def approved_run(self, born=date(2000, 3, 3)):
        if born:
            services.set_pay_details(self.hr, self.worker, {"date_of_birth": born})
        run = alerts.create_weekly_run(MONDAY_AFTER)
        for slip in run.payslips.all():
            services.set_deductions(self.hr, slip, {"income_tax": Decimal("44.80")}, True)
        services.approve_run(self.hr, run, PayslipTemplate.current())
        return run

    def test_the_payslip_email_carries_a_pdf_that_only_opens_with_the_password(self):
        self.approved_run()
        alerts.send_due_payslips(THURSDAY)
        send_everything()
        message = inbox(self.worker.user.email)[0]
        self.assertEqual(len(message.attachments), 1)
        name, content, mimetype = message.attachments[0]
        self.assertEqual((mimetype, name.startswith("payslip-2026-30-")), ("application/pdf", True))
        reader = PdfReader(BytesIO(content))
        self.assertTrue(reader.is_encrypted)
        self.assertEqual(reader.trailer["/Encrypt"]["/V"], 5)  # AES-256
        self.assertEqual(reader.decrypt("wrong030003"), 0)
        self.assertEqual(PdfReader(BytesIO(content)).decrypt("sam030003") > 0, True)
        reader = PdfReader(BytesIO(content))
        reader.decrypt("sam030003")
        text = reader.pages[0].extract_text()
        self.assertIn("£466.00", text)
        self.assertIn("£421.20", text)
        with self.assertRaises(Exception):
            PdfReader(BytesIO(content)).pages[0].extract_text()  # without the password the pages cannot be read

    def test_the_email_explains_the_password_rule_but_never_the_password(self):
        self.approved_run()
        alerts.send_due_payslips(THURSDAY)
        send_everything()
        body = inbox(self.worker.user.email)[0].body
        self.assertIn("mmyydd", body)
        self.assertIn("alex069005", body)  # a made-up worked example
        self.assertNotIn("sam030003", body)  # Sam's real password
        self.assertIn("Net pay: £421.20", body)

    def test_the_password_follows_the_date_of_birth_on_file(self):
        services.set_pay_details(self.hr, self.worker, {"date_of_birth": date(1990, 7, 15)})
        self.approved_run(born=None)
        alerts.send_due_payslips(THURSDAY)
        send_everything()
        reader = PdfReader(BytesIO(inbox(self.worker.user.email)[0].attachments[0][1]))
        self.assertEqual(reader.decrypt("sam030003"), 0)
        self.assertGreater(reader.decrypt("sam079015"), 0)

    def test_nothing_is_attached_without_a_date_of_birth_and_the_email_says_why(self):
        self.approved_run(born=None)
        alerts.send_due_payslips(THURSDAY)
        send_everything()
        message = inbox(self.worker.user.email)[0]
        self.assertEqual(message.attachments, [])
        self.assertIn("could not be attached", message.body)
        self.assertIn("https://hr.example/payslips", message.body)

    def test_nothing_is_attached_without_a_first_name(self):
        self.worker.user.first_name = ""
        self.worker.user.save()
        self.approved_run()
        alerts.send_due_payslips(THURSDAY)
        send_everything()
        self.assertEqual(inbox(self.worker.user.email)[0].attachments, [])

    def test_the_queued_email_holds_no_pdf_and_no_password(self):
        self.approved_run()
        alerts.send_due_payslips(THURSDAY)
        stored = Notification.objects.get(kind="payslip_ready")
        self.assertEqual(set(stored.context), {"period_start", "period_end", "pay_date", "hours", "net_pay", "payslip_id", "attach"})
        self.assertNotIn("030003", str(stored.context))

    def test_a_missing_pdf_file_means_a_retry_not_a_lost_or_unprotected_email(self):
        run = self.approved_run()
        alerts.send_due_payslips(THURSDAY)
        slip = run.payslips.get()
        slip.pdf.storage.delete(slip.pdf.name)
        send_everything()
        self.assertEqual(inbox(self.worker.user.email), [])
        item = Notification.objects.get(kind="payslip_ready")
        self.assertEqual((item.status, item.attempts), ("retry", 1))

    def test_a_payslip_pdf_is_never_attached_to_someone_elses_email(self):
        run = self.approved_run()
        alerts.send_due_payslips(THURSDAY)
        slip = run.payslips.get()
        self.assertEqual(attachments.payslip_pdf({"attach": True, "payslip_id": slip.pk}, self.emp_b.user), [])
        self.assertEqual(attachments.payslip_pdf({"attach": False, "payslip_id": slip.pk}, self.worker.user), [])
        self.assertEqual(len(attachments.payslip_pdf({"attach": True, "payslip_id": slip.pk}, self.worker.user)), 1)
