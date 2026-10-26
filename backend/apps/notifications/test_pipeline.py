import sys
from datetime import timedelta
from io import StringIO
from smtplib import SMTPException
from types import SimpleNamespace
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.accounts.models import Role
from apps.core.testutils import make_user
from apps.stores.models import Store

from . import api, delivery
from .bus import KafkaBus, MemoryBus
from .models import Notification, NotificationStatus


def queue(user, kind="swap_declined", key="k1", **extra):
    ctx = extra.pop("context", {
        "colleague": "Sam", "offered": {"date": "2026-11-02", "start": "09:00", "end": "17:00", "store": "Shop A", "role": ""},
    })
    api.notify(kind, [user], ctx, key)
    return Notification.objects.get(recipient=user, dedupe_key=f"{key}:{user.pk}")


class PipelineBase(TestCase):
    def setUp(self):
        MemoryBus.reset()
        self.user = make_user("worker@example.com", first_name="Pat")
        self.other = make_user("other@example.com")


class QueueTests(PipelineBase):
    def test_one_row_per_person_and_the_same_key_is_queued_once(self):
        self.assertEqual(api.notify("swap_declined", [self.user, self.other, self.user, None], {}, "a"), 2)
        api.notify("swap_declined", [self.user, self.other], {}, "a")
        self.assertEqual(Notification.objects.count(), 2)
        api.notify("swap_declined", [self.user], {}, "b")
        self.assertEqual(Notification.objects.count(), 3)

    def test_context_can_depend_on_the_person(self):
        api.notify("swap_declined", [self.user, self.other], lambda u: {"who": u.email}, "c")
        self.assertEqual(Notification.objects.get(recipient=self.other).context, {"who": "other@example.com"})

    def test_an_email_is_queued_only_if_the_change_really_happened(self):
        with self.assertRaises(RuntimeError), transaction.atomic():
            api.notify("swap_declined", [self.user], {}, "gone")
            raise RuntimeError("the change failed")
        self.assertEqual(Notification.objects.count(), 0)

    def test_managers_are_the_approvers_and_admins_are_the_fallback(self):
        owner = make_user("owner@example.com", Role.OWNER)
        hr = make_user("hr@example.com", Role.HR)
        manager = make_user("m@example.com", Role.MANAGER)
        gone = make_user("gone@example.com", Role.MANAGER, is_active=False)
        make_user("emp@example.com", Role.EMPLOYEE)
        store = Store.objects.create(name="S")
        self.assertEqual({u.pk for u in api.store_approvers(store)}, {owner.pk, hr.pk})
        store.managers.add(manager, gone)
        self.assertEqual([u.pk for u in api.store_approvers(store)], [manager.pk])


class RelayTests(PipelineBase):
    def test_due_emails_are_published_once_and_not_again_until_the_timeout(self):
        item = queue(self.user)
        bus = MemoryBus()
        self.assertEqual(delivery.relay_due(bus), 1)
        self.assertEqual(MemoryBus.messages, [str(item.pk)])
        self.assertEqual(delivery.relay_due(bus), 0)
        later = timezone.now() + delivery.VISIBILITY_TIMEOUT + timedelta(seconds=1)
        self.assertEqual(delivery.relay_due(bus, now=later), 1)  # nobody sent it, so it goes out again

    def test_finished_and_not_yet_due_emails_are_not_published(self):
        sent = queue(self.user, key="s")
        Notification.objects.filter(pk=sent.pk).update(status=NotificationStatus.SENT)
        waiting = queue(self.user, key="w")
        Notification.objects.filter(pk=waiting.pk).update(next_attempt_at=timezone.now() + timedelta(hours=1))
        self.assertEqual(delivery.relay_due(MemoryBus()), 0)

    def test_only_what_kafka_confirmed_counts_as_published(self):
        item = queue(self.user)
        bus = mock.Mock()
        bus.publish.return_value = []
        self.assertEqual(delivery.relay_due(bus), 0)
        item.refresh_from_db()
        self.assertIsNone(item.published_at)  # so the next run tries again


class DeliveryTests(PipelineBase):
    def test_the_email_goes_to_the_right_person(self):
        item = queue(self.user)
        delivery.deliver(item.pk)
        item.refresh_from_db()
        self.assertEqual(item.status, NotificationStatus.SENT)
        self.assertIsNotNone(item.sent_at)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["worker@example.com"])
        self.assertTrue(message.body.startswith("Hi Pat,"))
        self.assertEqual(message.subject, "Your swap request was declined")

    def test_receiving_the_same_message_twice_sends_one_email(self):
        item = queue(self.user)
        delivery.deliver(item.pk)
        delivery.deliver(str(item.pk))
        self.assertEqual(len(mail.outbox), 1)

    def test_inactive_people_and_garbage_ids_are_handled(self):
        self.user.is_active = False
        self.user.save()
        item = queue(self.user)
        delivery.deliver(item.pk)
        item.refresh_from_db()
        self.assertEqual((item.status, len(mail.outbox)), (NotificationStatus.SKIPPED, 0))
        self.assertIsNone(delivery.deliver("not-a-uuid"))
        self.assertIsNone(delivery.deliver("00000000-0000-0000-0000-000000000000"))

    def test_an_unknown_kind_fails_straight_away(self):
        item = queue(self.user, kind="made_up")
        delivery.deliver(item.pk)
        item.refresh_from_db()
        self.assertEqual(item.status, NotificationStatus.FAILED)
        self.assertEqual(len(mail.outbox), 0)

    def test_a_mail_server_problem_means_a_later_retry_not_a_lost_email(self):
        item = queue(self.user)
        now = timezone.now()
        with mock.patch("apps.notifications.delivery.send_mail", side_effect=SMTPException("server busy")):
            delivery.deliver(item.pk, now=now)
        item.refresh_from_db()
        self.assertEqual((item.status, item.attempts), (NotificationStatus.RETRY, 1))
        self.assertEqual(item.next_attempt_at, now + timedelta(minutes=2))
        self.assertIsNone(item.published_at)
        self.assertIn("SMTPException", item.last_error)
        self.assertEqual(delivery.relay_due(MemoryBus(), now=now), 0)  # not due yet
        self.assertEqual(delivery.relay_due(MemoryBus(), now=now + timedelta(minutes=3)), 1)

    def test_an_email_that_failed_after_being_published_is_published_again_for_the_retry(self):
        item = queue(self.user)
        now = timezone.now()
        delivery.relay_due(MemoryBus(), now=now)  # on the bus, so published_at is set
        with mock.patch("apps.notifications.delivery.send_mail", side_effect=SMTPException("server busy")):
            delivery.deliver(item.pk, now=now)
        # due again in 3 minutes, well inside the 10-minute window that would otherwise hide it
        self.assertEqual(delivery.relay_due(MemoryBus(), now=now + timedelta(minutes=3)), 1)

    def test_it_gives_up_after_five_attempts_and_a_later_success_clears_the_error(self):
        item = queue(self.user)
        with mock.patch("apps.notifications.delivery.send_mail", side_effect=SMTPException("down")):
            for _ in range(delivery.MAX_ATTEMPTS):
                delivery.deliver(item.pk)
        item.refresh_from_db()
        self.assertEqual((item.status, item.attempts), (NotificationStatus.FAILED, delivery.MAX_ATTEMPTS))
        other = queue(self.other, key="ok")
        with mock.patch("apps.notifications.delivery.send_mail", side_effect=[SMTPException("x"), 1]):
            delivery.deliver(other.pk)
            delivery.deliver(other.pk)
        other.refresh_from_db()
        self.assertEqual((other.status, other.last_error), (NotificationStatus.SENT, ""))

    def test_a_failed_email_is_never_retried_by_the_relay(self):
        item = queue(self.user)
        Notification.objects.filter(pk=item.pk).update(status=NotificationStatus.FAILED)
        self.assertEqual(delivery.relay_due(MemoryBus()), 0)

    def test_subjects_cannot_carry_line_breaks(self):
        from .emails import clean_subject

        self.assertEqual(clean_subject("Hello\r\nBcc: attacker@example.com"), "Hello Bcc: attacker@example.com")

    def test_the_whole_path_from_queue_to_inbox(self):
        queue(self.user)
        bus = MemoryBus()
        delivery.relay_due(bus)
        bus.consume(delivery.deliver)
        self.assertEqual([m.to for m in mail.outbox], [["worker@example.com"]])

    @override_settings(NOTIFICATIONS_BUS="memory")
    def test_the_two_commands_do_the_same_job(self):
        queue(self.user)
        call_command("relay_notifications", "--once", stdout=StringIO())
        call_command("consume_notifications", "--once", stdout=StringIO())
        self.assertEqual(len(mail.outbox), 1)


class KafkaAdapterTests(PipelineBase):
    """The adapter is checked against a stand-in for the Kafka library: no broker runs in the tests."""

    def fake_library(self, deliver_ok=True, messages=()):
        produced, commits = [], []

        class Producer:
            def __init__(self, config):
                produced.append(("config", config))

            def produce(self, topic, key, value, callback):
                produced.append((topic, key, value))
                error = None if deliver_ok else "broker unavailable"
                callback(error, SimpleNamespace(key=lambda: key.encode()))

            def flush(self, timeout):
                produced.append(("flush", timeout))

        queued = list(messages)

        class Consumer:
            def __init__(self, config):
                self.config = config

            def subscribe(self, topics):
                self.topics = topics

            def poll(self, timeout):
                return queued.pop(0) if queued else None

            def commit(self, message, asynchronous):
                commits.append((message, asynchronous))

            def close(self):
                commits.append("closed")

        library = SimpleNamespace(Producer=Producer, Consumer=Consumer)
        return library, produced, commits

    def message(self, text, error=None):
        return SimpleNamespace(value=lambda: text.encode(), error=lambda: error)

    def test_publishing_sends_the_ids_with_safe_settings(self):
        library, produced, _ = self.fake_library()
        with mock.patch.dict(sys.modules, {"confluent_kafka": library}):
            confirmed = KafkaBus("kafka:9092", "topic-x", "group-x").publish(["id-1", "id-2"])
        self.assertEqual(confirmed, ["id-1", "id-2"])
        self.assertIn(("topic-x", "id-1", "id-1"), produced)
        config = produced[0][1]
        self.assertEqual((config["acks"], config["enable.idempotence"]), ("all", True))

    def test_ids_kafka_did_not_accept_are_not_reported_as_published(self):
        library, _, _ = self.fake_library(deliver_ok=False)
        with mock.patch.dict(sys.modules, {"confluent_kafka": library}):
            self.assertEqual(KafkaBus("k:1", "t", "g").publish(["id-1"]), [])

    def test_consuming_commits_each_message_after_handling_it_and_survives_a_bad_one(self):
        library, _, commits = self.fake_library(messages=[self.message("a"), self.message("b"), self.message("c")])
        seen = []

        def handler(text):
            seen.append(text)
            if text == "b":
                raise RuntimeError("boom")

        with mock.patch.dict(sys.modules, {"confluent_kafka": library}):
            KafkaBus("k:1", "t", "g").consume(handler, once=True, idle_seconds=0)
        self.assertEqual(seen, ["a", "b", "c"])
        self.assertEqual(len([c for c in commits if c != "closed"]), 3)
        self.assertEqual(commits[-1], "closed")

    def test_once_waits_through_empty_polls_while_the_consumer_joins_its_group(self):
        library, _, _ = self.fake_library(messages=[None, None, self.message("a")])
        seen = []
        with mock.patch.dict(sys.modules, {"confluent_kafka": library}):
            KafkaBus("k:1", "t", "g").consume(seen.append, once=True, idle_seconds=5)
        self.assertEqual(seen, ["a"])

    def test_automatic_commit_is_off_so_a_crash_means_redelivery(self):
        library, _, _ = self.fake_library()
        captured = {}
        original = library.Consumer

        class Spy(original):
            def __init__(self, config):
                captured.update(config)
                super().__init__(config)

        library.Consumer = Spy
        with mock.patch.dict(sys.modules, {"confluent_kafka": library}):
            KafkaBus("k:1", "t", "g").consume(lambda text: None, once=True, idle_seconds=0)
        self.assertIs(captured["enable.auto.commit"], False)


class StatusViewTests(PipelineBase, APITestCase):
    def test_only_owner_and_hr_can_look(self):
        for role, expected in ((Role.OWNER, 200), (Role.HR, 200), (Role.MANAGER, 403), (Role.EMPLOYEE, 403)):
            self.client.force_authenticate(make_user(f"{role}@example.com", role))
            self.assertEqual(self.client.get(reverse("notification-status")).status_code, expected)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(reverse("notification-status")).status_code, 401)

    def test_counts_age_and_failures(self):
        queue(self.user, key="a")
        broken = queue(self.user, key="b")
        Notification.objects.filter(pk=broken.pk).update(status=NotificationStatus.FAILED, last_error="SMTPException: x")
        self.client.force_authenticate(make_user("boss@example.com", Role.OWNER))
        body = self.client.get(reverse("notification-status")).data
        self.assertEqual((body["counts"]["pending"], body["counts"]["failed"], body["counts"]["sent"]), (1, 1, 0))
        self.assertIsNotNone(body["oldest_waiting_minutes"])
        self.assertEqual(body["recent_failures"][0]["last_error"], "SMTPException: x")
        self.assertNotIn("context", body["recent_failures"][0])
