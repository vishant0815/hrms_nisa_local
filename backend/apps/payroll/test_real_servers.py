"""Tests against a real SMTP server and a real Kafka broker, for when they are available.

They are skipped on an ordinary run. To run them:
  SMTP:  pip install aiosmtpd   (a small mail server is started inside the test; nothing leaves the machine)
  Kafka: HRMS_TEST_KAFKA=localhost:9092 python manage.py test apps.payroll.test_real_servers
"""
import email
import email.policy
import os
import socket
import uuid
from datetime import date
from decimal import Decimal
from io import BytesIO

from django.test import override_settings
from pypdf import PdfReader
from unittest import skipUnless

from apps.notifications import delivery
from apps.notifications.bus import KafkaBus
from apps.notifications.models import Notification, NotificationStatus
from apps.payroll import alerts, services
from apps.payroll.models import PayslipTemplate
from apps.payroll.test_payruns import PayBase
from apps.payroll.test_schedule import MONDAY_AFTER, THURSDAY

try:
    from aiosmtpd.controller import Controller
except ImportError:  # pragma: no cover
    Controller = None

KAFKA = os.environ.get("HRMS_TEST_KAFKA", "")


class Inbox:
    def __init__(self):
        self.messages = []

    async def handle_DATA(self, server, session, envelope):
        self.messages.append((envelope.mail_from, list(envelope.rcpt_tos), envelope.content))
        return "250 OK"


class RealBase(PayBase):
    def setUp(self):
        super().setUp()
        self.sample_week()
        self.worker.user.first_name = "Sam"
        self.worker.user.save()
        run = alerts.create_weekly_run(MONDAY_AFTER)
        for slip in run.payslips.all():
            services.set_deductions(self.hr, slip, {"income_tax": Decimal("44.80")}, True)
        services.approve_run(self.hr, run, PayslipTemplate.current())
        services.set_pay_details(self.hr, self.worker, {"date_of_birth": date(2000, 3, 3)})
        alerts.send_due_payslips(THURSDAY)  # queues the email


@skipUnless(Controller, "aiosmtpd is not installed")
class RealSmtpTests(RealBase):
    def test_the_payslip_email_and_its_protected_pdf_arrive_through_a_real_smtp_conversation(self):
        inbox = Inbox()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        server = Controller(inbox, hostname="127.0.0.1", port=port)
        server.start()
        try:
            with override_settings(
                EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend", EMAIL_HOST="127.0.0.1",
                EMAIL_PORT=port, EMAIL_USE_TLS=False,
                EMAIL_HOST_USER="", EMAIL_HOST_PASSWORD="",
            ):
                item = Notification.objects.get(kind="payslip_ready")
                delivery.deliver(item.pk)
        finally:
            server.stop()
        item.refresh_from_db()
        self.assertEqual(item.status, NotificationStatus.SENT, item.last_error)
        self.assertEqual(len(inbox.messages), 1)
        sender, recipients, raw = inbox.messages[0]
        self.assertEqual(recipients, [self.worker.user.email])
        message = email.message_from_bytes(raw, policy=email.policy.default)
        self.assertIn("payslip", message["Subject"].lower())
        pdfs = [part for part in message.walk() if part.get_content_type() == "application/pdf"]
        self.assertEqual(len(pdfs), 1)
        reader = PdfReader(BytesIO(pdfs[0].get_payload(decode=True)))
        self.assertTrue(reader.is_encrypted)
        self.assertEqual(reader.decrypt("wrong030003"), 0)
        self.assertGreater(reader.decrypt("sam030003"), 0)
        self.assertIn("£466.00", reader.pages[0].extract_text())
        body = message.get_body(("plain",)).get_content()
        self.assertNotIn("sam030003", body)

    def test_a_refused_connection_leaves_the_email_to_be_tried_again(self):
        with override_settings(
            EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend", EMAIL_HOST="127.0.0.1", EMAIL_PORT=1,
            EMAIL_USE_TLS=False,
        ):
            item = Notification.objects.get(kind="payslip_ready")
            delivery.deliver(item.pk)
        item.refresh_from_db()
        self.assertEqual(item.status, NotificationStatus.RETRY)
        self.assertEqual(item.attempts, 1)


@skipUnless(KAFKA, "set HRMS_TEST_KAFKA to a broker address to run the Kafka tests")
class RealKafkaTests(RealBase):
    def bus(self):
        name = uuid.uuid4().hex[:8]
        return KafkaBus(servers=KAFKA, topic=f"test-notifications-{name}", group=f"test-group-{name}")

    def test_the_relay_publishes_to_kafka_and_the_consumer_delivers_exactly_once(self):
        from django.core import mail

        bus = self.bus()
        mail.outbox = []
        published = delivery.relay_due(bus)  # the payslip email and the Monday/Thursday notices for Owner and HR
        self.assertEqual(published, Notification.objects.exclude(published_at=None).count())
        self.assertGreaterEqual(published, 1)
        item = Notification.objects.get(kind="payslip_ready")
        self.assertIsNotNone(item.published_at)
        bus.consume(delivery.deliver, once=True)
        item.refresh_from_db()
        self.assertEqual(item.status, NotificationStatus.SENT)
        self.assertEqual(len([m for m in mail.outbox if m.to == [self.worker.user.email]]), 1)
        self.assertEqual(delivery.relay_due(bus), 0)  # already sent: nothing more to publish

    def test_a_message_delivered_twice_sends_one_email(self):
        from django.core import mail

        bus = self.bus()
        mail.outbox = []
        item = Notification.objects.get(kind="payslip_ready")
        bus.publish([str(item.pk), str(item.pk)])
        bus.consume(delivery.deliver, once=True)
        self.assertEqual(len([m for m in mail.outbox if m.to == [self.worker.user.email]]), 1)

    def test_nothing_is_marked_published_when_the_broker_cannot_be_reached(self):
        down = KafkaBus(servers="127.0.0.1:1", topic="x", group="y")
        self.assertEqual(delivery.relay_due(down), 0)
        self.assertIsNone(Notification.objects.get(kind="payslip_ready").published_at)


@skipUnless(Controller and KAFKA, "needs aiosmtpd and HRMS_TEST_KAFKA")
class RealKafkaToRealSmtpTests(RealBase):
    def test_a_queued_payslip_goes_through_kafka_and_out_by_smtp_with_its_protected_pdf(self):
        name = uuid.uuid4().hex[:8]
        bus = KafkaBus(servers=KAFKA, topic=f"test-e2e-{name}", group=f"test-e2e-{name}")
        inbox = Inbox()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        server = Controller(inbox, hostname="127.0.0.1", port=port)
        server.start()
        try:
            with override_settings(
                EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend", EMAIL_HOST="127.0.0.1", EMAIL_PORT=port,
                EMAIL_USE_TLS=False,
            ):
                delivery.relay_due(bus)
                bus.consume(delivery.deliver, once=True)
        finally:
            server.stop()
        mine = [m for m in inbox.messages if m[1] == [self.worker.user.email]]
        self.assertEqual(len(mine), 1)
        message = email.message_from_bytes(mine[0][2], policy=email.policy.default)
        pdf = next(p for p in message.walk() if p.get_content_type() == "application/pdf")
        reader = PdfReader(BytesIO(pdf.get_payload(decode=True)))
        self.assertGreater(reader.decrypt("sam030003"), 0)
        self.assertEqual(Notification.objects.filter(status=NotificationStatus.SENT).count(), len(inbox.messages))
