"""Each event queues the right emails for the right people, and nothing private leaks."""
from io import StringIO

from django.core import mail
from django.core.management import call_command
from django.urls import reverse

from apps.attendance.tests_base import FAR, AttendanceBase, at
from apps.core.testutils import days_from_today, verified_term_letter
from apps.rota import services as rota_services
from apps.rota.test_swaps import SwapBase
from apps.rota.tests import RotaTestBase
from apps.visa import letters
from apps.visa.test_term_letters import TermLetterBase, upload
from apps.visa.tests import VisaTestBase

from . import delivery
from .bus import MemoryBus
from .models import Notification

IMMIGRATION_WORDS = ("visa", "immigration", "right to work", "passport", "student", "sponsor", "home office")


def send_everything():
    MemoryBus.reset()
    bus = MemoryBus()
    delivery.relay_due(bus)
    bus.consume(delivery.deliver)


def queued(kind):
    return sorted(Notification.objects.filter(kind=kind).values_list("recipient__email", flat=True))


def inbox(email):
    return [m for m in mail.outbox if m.to == [email]]


class RotaPublishedTests(RotaTestBase):
    def test_each_person_gets_their_own_shifts_and_nobody_else_gets_anything(self):
        from datetime import time

        from apps.rota.tests import day

        week = self.week_a
        rota_services.create_shift(rota_week=week, by=self.hr, employee=self.emp_a, date=day(0),
                                   start_time=time(9), end_time=time(17), notes="private manager note")
        rota_services.create_shift(rota_week=week, by=self.hr, employee=self.emp_a, date=day(1),
                                   start_time=time(10), end_time=time(14))
        rota_services.create_shift(rota_week=week, by=self.hr, employee=None, date=day(2),
                                   start_time=time(9), end_time=time(17))  # an open shift
        self.assertEqual(Notification.objects.count(), 0)  # a draft tells nobody anything
        rota_services.publish_week(rota_week=week, by=self.hr)
        self.assertEqual(queued("rota_published"), ["emp-a@example.com"])
        send_everything()
        body = inbox("emp-a@example.com")[0].body
        self.assertIn("09:00-17:00", body)
        self.assertIn("10:00-14:00", body)
        self.assertIn("Shop A", body)
        self.assertNotIn("private manager note", body)
        self.assertEqual(inbox("emp-b@example.com"), [])

    def test_shifts_that_have_already_passed_are_left_out(self):
        from datetime import time

        from apps.rota.tests import day, pretend_today

        for n in (0, 3):
            rota_services.create_shift(rota_week=self.week_a, by=self.hr, employee=self.emp_a, date=day(n),
                                       start_time=time(9), end_time=time(17))
        with pretend_today(day(2)):
            rota_services.publish_week(rota_week=self.week_a, by=self.hr)
        send_everything()
        body = inbox("emp-a@example.com")[0].body
        self.assertIn(day(3).strftime("%a %d %b") + ", 09:00-17:00", body)
        self.assertNotIn(day(0).strftime("%a %d %b") + ", 09:00-17:00", body)

    def test_a_week_with_no_upcoming_shifts_sends_nothing(self):
        rota_services.publish_week(rota_week=self.week_a, by=self.hr)
        self.assertEqual(Notification.objects.count(), 0)


class SwapEmailTests(SwapBase):
    def test_the_whole_swap_story_reaches_the_right_inboxes(self):
        swap_id = self.request(self.emp_a.user, self.s_a0, self.emp_c, note="x").data["id"]
        self.assertEqual(queued("swap_requested"), ["emp-c@example.com"])

        self.act(self.emp_c.user, "accept", swap_id, in_return_shift=self.s_c2.pk)
        self.assertEqual(queued("swap_accepted_manager"), ["mgr-a@example.com"])  # not Shop B's manager
        self.assertEqual(queued("swap_accepted_requester"), ["emp-a@example.com"])

        self.decide(self.manager_a, "approve", swap_id)
        self.assertEqual(queued("swap_approved"), ["emp-a@example.com", "emp-c@example.com"])
        send_everything()
        subjects = lambda email: sorted(m.subject for m in inbox(email) if "swap" in m.subject.lower() or "shift" in m.subject.lower())  # noqa: E731
        self.assertEqual(subjects("emp-a@example.com"), ["Your shift swap was approved", "Your swap is waiting for a manager"])
        self.assertEqual(subjects("emp-c@example.com"), ["A colleague has asked you to take a shift", "Your shift swap was approved"])
        approved_a = [m for m in inbox("emp-a@example.com") if "approved" in m.subject][0].body
        approved_c = [m for m in inbox("emp-c@example.com") if "approved" in m.subject][0].body
        self.assertIn("You now work", approved_a)
        self.assertIn("You no longer work", approved_a)
        self.assertNotEqual(approved_a, approved_c)  # each is told their own side

    def test_declined_cancelled_and_rejected(self):
        first = self.request(self.emp_a.user, self.s_a0, self.emp_c).data["id"]
        self.act(self.emp_c.user, "decline", first)
        self.assertEqual(queued("swap_declined"), ["emp-a@example.com"])

        second = self.request(self.emp_a.user, self.s_a1, self.emp_c).data["id"]
        self.act(self.emp_a.user, "cancel", second)
        self.assertEqual(queued("swap_cancelled"), ["emp-c@example.com"])

        third = self.waiting_for_manager(offered=self.s_a0)
        self.decide(self.manager_a, "reject", third.pk, reason="We are short that day")
        self.assertEqual(queued("swap_rejected"), ["emp-a@example.com", "emp-c@example.com"])
        send_everything()
        rejection = [m for m in inbox("emp-a@example.com") if "not approved" in m.subject][0]
        self.assertIn("We are short that day", rejection.body)

    def test_an_approval_that_is_blocked_queues_no_good_news(self):
        from apps.visa import services as visa_services

        swap = self.waiting_for_manager()
        visa_services.revoke_visa(visa=self.visa_c, by=self.hr, reason="x")
        self.assertEqual(self.decide(self.manager_a, "approve", swap.pk).status_code, 400)
        self.assertEqual(queued("swap_approved"), [])

    def test_a_refused_request_queues_nothing(self):
        self.assertEqual(self.request(self.emp_a.user, self.s_c2, self.emp_c).status_code, 400)
        self.assertEqual(Notification.objects.filter(kind="swap_requested").count(), 0)

    def test_swap_emails_never_mention_immigration_or_private_notes(self):
        swap_id = self.request(self.emp_a.user, self.s_a0, self.emp_c).data["id"]
        self.act(self.emp_c.user, "accept", swap_id, in_return_shift=self.s_c2.pk)
        self.decide(self.manager_a, "approve", swap_id)
        send_everything()
        for message in mail.outbox:
            text = (message.subject + message.body).lower()
            for word in IMMIGRATION_WORDS:
                self.assertNotIn(word, text)
            self.assertNotIn("private manager note", text)
            self.assertNotIn("@example.com", message.body)  # people are named, never by email address


class AttendanceEmailTests(AttendanceBase):
    def test_a_flagged_clock_in_goes_to_that_stores_manager_only(self):
        self.clock_in(where=FAR)
        self.assertEqual(queued("attendance_review"), ["mgr-a@example.com"])
        send_everything()
        body = inbox("mgr-a@example.com")[0].body
        self.assertIn("EMP-0001", body)
        self.assertIn("Clocked away from the shop.", body)
        self.assertIn("Shop A", body)

    def test_a_clean_clock_in_tells_nobody(self):
        self.clock_in()
        self.clock_out("17:00")
        self.assertEqual(Notification.objects.filter(kind="attendance_review").count(), 0)

    def test_a_problem_at_clock_out_is_reported_once(self):
        self.clock_in()
        self.clock_out("17:00", where=FAR)
        self.assertEqual(Notification.objects.filter(kind="attendance_review").count(), 1)
        send_everything()
        self.assertIn("At clock-out", inbox("mgr-a@example.com")[0].body)

    def test_the_email_lists_only_the_things_that_need_a_decision(self):
        self.clock_in()
        self.clock_out("15:00", where=FAR)  # far away (needs a decision) and early (just noted)
        send_everything()
        body = inbox("mgr-a@example.com")[0].body
        self.assertIn("away from the shop", body)
        self.assertNotIn("before the shift ended", body)

    def test_managers_are_not_told_twice_about_one_entry(self):
        self.clock_in(where=FAR)
        self.clock_out("17:00", where=FAR)  # already waiting for review
        self.assertEqual(Notification.objects.filter(kind="attendance_review").count(), 1)

    def test_a_store_without_managers_falls_back_to_owner_and_hr(self):
        self.store_a.managers.clear()
        self.clock_in(where=FAR)
        self.assertEqual(queued("attendance_review"), ["hr@example.com", "owner@example.com"])

    def test_informational_flags_do_not_send_email(self):
        self.clock_in()
        self.clock_out("15:00")  # left early: noted, no review
        self.assertEqual(Notification.objects.filter(kind="attendance_review").count(), 0)


class TermLetterEmailTests(TermLetterBase):
    def test_upload_tells_owner_and_hr_only(self):
        self.post_letter()
        self.assertEqual(queued("term_letter_uploaded"), ["hr@example.com", "owner@example.com"])
        send_everything()
        self.assertEqual(inbox("mgr-a@example.com"), [])
        self.assertIn("EMP-0001", inbox("hr@example.com")[0].body)

    def test_the_student_hears_the_outcome_and_the_reason(self):
        accepted = self.pending_letter()
        self.login(self.hr)
        self.verify(accepted)
        self.assertEqual(queued("term_letter_verified"), ["emp@example.com"])
        rejected = self.pending_letter()
        self.login(self.hr)
        self.client.post(reverse("term-letter-reject", args=[rejected]), {"reason": "The dates are cut off"}, format="json")
        self.assertEqual(queued("term_letter_rejected"), ["emp@example.com"])
        send_everything()
        reasons = [m.body for m in inbox("emp@example.com") if "not accepted" in m.subject]
        self.assertIn("The dates are cut off", reasons[0])

    def test_a_refused_upload_queues_nothing(self):
        self.post_letter(file=upload(b"<html>"))
        self.assertEqual(Notification.objects.count(), 0)


class VisaDigestTests(VisaTestBase):
    def run_digest(self, *args):
        call_command("send_visa_expiry_digest", *args, stdout=StringIO())

    def test_hr_and_owner_get_one_list_and_nobody_else(self):
        self.give_visa(self.employee, "Skilled Worker", expiry=days_from_today(20))
        self.run_digest()
        self.assertEqual(queued("visa_digest"), ["hr@example.com", "owner@example.com"])
        send_everything()
        body = inbox("hr@example.com")[0].body
        self.assertIn("Skilled Worker", body)
        self.assertIn("EMP-0001", body)
        self.assertEqual(inbox("mgr-a@example.com"), [])
        self.assertEqual(inbox("emp@example.com"), [])

    def test_it_lists_students_with_no_letter_and_drops_them_once_covered(self):
        visa = self.give_visa(self.employee, "Student", expiry=days_from_today(300))
        self.run_digest()
        send_everything()
        self.assertIn("no valid term-dates letter", inbox("hr@example.com")[0].body)
        mail.outbox.clear()
        Notification.objects.all().delete()
        verified_term_letter(visa, self.hr, days_from_today(-30), days_from_today(100))
        Notification.objects.all().delete()
        self.run_digest()
        self.assertEqual(Notification.objects.count(), 0)

    def test_nothing_to_report_means_no_email_and_it_runs_once_a_day(self):
        self.run_digest()
        self.assertEqual(Notification.objects.count(), 0)
        self.give_visa(self.employee, "Skilled Worker", expiry=days_from_today(5))
        self.run_digest()
        self.run_digest()
        self.assertEqual(Notification.objects.filter(kind="visa_digest").count(), 2)  # one each for HR and Owner

    def test_a_new_day_gets_a_new_digest(self):
        from datetime import timedelta
        from unittest import mock

        from django.utils import timezone

        self.give_visa(self.employee, "Skilled Worker", expiry=days_from_today(5))
        self.run_digest()
        self.assertEqual(Notification.objects.count(), 2)
        tomorrow = mock.MagicMock()
        tomorrow.localdate.return_value = timezone.localdate() + timedelta(days=1)
        with mock.patch("apps.visa.management.commands.send_visa_expiry_digest.timezone", tomorrow):
            self.run_digest()
        self.assertEqual(Notification.objects.count(), 4)

    def test_the_look_ahead_window_is_respected(self):
        self.give_visa(self.employee, "Skilled Worker", expiry=days_from_today(100))
        self.run_digest("--days", "30")
        self.assertEqual(Notification.objects.count(), 0)
        self.run_digest("--days", "120")
        self.assertEqual(Notification.objects.filter(kind="visa_digest").count(), 2)
