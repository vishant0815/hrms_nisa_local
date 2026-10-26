from datetime import timedelta
from unittest import mock

from django.db import IntegrityError, transaction
from django.test import SimpleTestCase
from django.urls import reverse
from rest_framework.exceptions import ValidationError

from apps.accounts.models import Role
from apps.attendance import services as attendance
from apps.attendance.models import AttendanceEdit, AttendanceRecord, ReviewStatus
from apps.attendance.tests_base import AttendanceBase, at
from apps.core.testutils import make_user
from apps.notifications.test_alerts import inbox, queued, send_everything
from apps.stores.models import Store

from . import breaks, services
from .models import BreakPeriod, ClockDevice, Enrolment, Punch, PunchOutcome


class AllowanceTests(SimpleTestCase):
    def test_the_break_allowance_for_each_shift_length(self):
        for minutes, allowed in [(0, 0), (359, 0), (360, 30), (480, 30), (720, 30), (721, 60), (780, 60)]:
            self.assertEqual(breaks.allowance_minutes(minutes), allowed, minutes)


class FingerprintBase(AttendanceBase):
    def setUp(self):
        super().setUp()
        self.device, self.secret = services.register_device(store=self.store_a, name="Front door", serial="ZK-0001")
        self.enrolment = services.enrol(employee=self.emp_a, by=self.hr, consent_note="Signed form, HR folder")

    # --- helpers ---
    def worker(self, email, start, end, n=0, store=None):
        store = store or self.store_a
        employee = self.hire(email, store)
        self.clear(employee)
        week = self.week_a if store == self.store_a else self.week_b
        self.shift(employee, n, start, end, week)
        return employee, services.enrol(employee=employee, by=self.hr, consent_note="Signed form")

    def punch(self, kind, when, enrolment=None, device=None, n=0, device_time=None, received=None):
        return services.process_punch(
            device=device or self.device, user_id=(enrolment or self.enrolment).device_user_id, kind=kind,
            device_time=device_time or at(n, when), received_at=received or at(n, when),
        )

    def record(self, employee=None):
        return AttendanceRecord.objects.get(employee=employee or self.emp_a)

    def flags(self, employee=None):
        return self.record(employee).flags

    def day_of_breaks(self, minutes, shift=("09:00", "17:00"), start_break="12:00"):
        """Clock in at the shift start, take a break of `minutes`, clock out at the shift end."""
        self.punch("in", shift[0])
        if minutes:
            hours, mins = map(int, start_break.split(":"))
            end_total = hours * 60 + mins + minutes
            self.punch("break_start", start_break)
            self.punch("break_end", f"{end_total // 60:02d}:{end_total % 60:02d}")
        self.punch("out", shift[1])
        return self.record()


class DeviceTests(FingerprintBase):
    def test_the_secret_is_shown_once_and_only_a_hash_is_kept(self):
        self.assertNotEqual(self.device.secret_hash, self.secret)
        self.assertEqual(self.device.secret_hash, services.digest(self.secret))
        self.assertEqual(services.authenticate("ZK-0001", self.secret), self.device)

    def test_a_wrong_secret_an_unknown_serial_or_a_switched_off_device_is_refused(self):
        self.assertIsNone(services.authenticate("ZK-0001", "wrong"))
        self.assertIsNone(services.authenticate("ZK-0001", ""))
        self.assertIsNone(services.authenticate("ZK-9999", self.secret))
        ClockDevice.objects.filter(pk=self.device.pk).update(is_active=False)
        self.assertIsNone(services.authenticate("ZK-0001", self.secret))

    def test_one_secret_cannot_be_used_for_another_device(self):
        other, other_secret = services.register_device(store=self.store_b, name="Back", serial="ZK-0002")
        self.assertIsNone(services.authenticate("ZK-0001", other_secret))
        self.assertEqual(services.authenticate("ZK-0002", other_secret), other)

    def test_rotating_the_secret_stops_the_old_one_working(self):
        new = services.rotate_secret(self.device)
        self.assertIsNone(services.authenticate("ZK-0001", self.secret))
        self.assertIsNotNone(services.authenticate("ZK-0001", new))

    def test_a_serial_can_only_be_registered_once(self):
        with self.assertRaises(ValidationError):
            services.register_device(store=self.store_b, name="Copy", serial="ZK-0001")

    def test_owner_and_hr_register_devices_and_the_secret_appears_only_then(self):
        self.login(self.hr)
        response = self.client.post(reverse("clock-devices"), {"store": self.store_b.pk, "name": "Back", "serial": "ZK-0002"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(services.authenticate("ZK-0002", response.data["secret"]).store, self.store_b)
        listing = self.client.get(reverse("clock-devices")).data
        self.assertEqual(len(listing), 2)
        self.assertTrue(all("secret" not in row and "secret_hash" not in row for row in listing))

    def test_bad_serials_are_refused(self):
        self.login(self.hr)
        for serial in ("ab", "has space", "x" * 65, "<script>"):
            response = self.client.post(reverse("clock-devices"), {"store": self.store_a.pk, "name": "x", "serial": serial})
            self.assertEqual(response.status_code, 400, serial)

    def test_managers_see_only_their_own_shops_devices_and_cannot_change_them(self):
        services.register_device(store=self.store_b, name="Back", serial="ZK-0002")
        self.login(self.manager_a)
        self.assertEqual([row["serial"] for row in self.client.get(reverse("clock-devices")).data], ["ZK-0001"])
        self.assertEqual(self.client.post(reverse("clock-devices"), {"store": self.store_a.pk, "name": "x", "serial": "ZK-0003"}).status_code, 403)
        self.assertEqual(self.client.patch(reverse("clock-device-detail", args=[self.device.pk]), {"is_active": False}).status_code, 403)
        self.assertEqual(self.client.post(reverse("clock-device-secret", args=[self.device.pk])).status_code, 403)
        self.login(self.emp_a.user)
        self.assertEqual(self.client.get(reverse("clock-devices")).status_code, 403)

    def test_switching_a_device_off_stops_it_working(self):
        self.login(self.owner)
        self.client.patch(reverse("clock-device-detail", args=[self.device.pk]), {"is_active": False})
        self.assertIsNone(services.authenticate("ZK-0001", self.secret))

    def test_online_means_heard_from_in_the_last_ten_minutes(self):
        now = at(0, "09:00")
        services.note_contact(self.device, now - timedelta(seconds=90), now)
        self.device.refresh_from_db()
        self.assertEqual(self.device.clock_skew_seconds, 90)
        with mock.patch("apps.fingerprint.models.timezone.now", return_value=now + timedelta(minutes=9)):
            self.assertTrue(self.device.online)
        with mock.patch("apps.fingerprint.models.timezone.now", return_value=now + timedelta(minutes=11)):
            self.assertFalse(self.device.online)


class EnrolmentTests(FingerprintBase):
    def test_numbers_count_up_and_are_never_reused(self):
        self.assertEqual(self.enrolment.device_user_id, 1)
        services.withdraw(enrolment=self.enrolment, by=self.hr, reason="Prefers the app")
        again = services.enrol(employee=self.emp_a, by=self.hr, consent_note="New form")
        self.assertEqual(again.device_user_id, 2)
        other = services.enrol(employee=self.emp_b, by=self.hr, consent_note="Form")
        self.assertEqual(other.device_user_id, 3)

    def test_consent_must_be_recorded_and_nobody_is_enrolled_twice(self):
        with self.assertRaises(ValidationError):
            services.enrol(employee=self.emp_b, by=self.hr, consent_note="  ")
        with self.assertRaises(ValidationError):
            services.enrol(employee=self.emp_a, by=self.hr, consent_note="Again")

    def test_the_database_also_refuses_two_active_enrolments(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Enrolment.objects.create(employee=self.emp_a, device_user_id=99, consent_note="x")

    def test_withdrawing_twice_is_refused(self):
        services.withdraw(enrolment=self.enrolment, by=self.hr, reason="x")
        with self.assertRaises(ValidationError):
            services.withdraw(enrolment=self.enrolment, by=self.hr, reason="x")

    def test_only_owner_and_hr_manage_enrolments(self):
        for user in (self.manager_a, self.emp_a.user):
            self.login(user)
            self.assertEqual(self.client.get(reverse("fingerprint-enrolments")).status_code, 403)
            self.assertEqual(self.client.post(reverse("fingerprint-enrolments"), {"employee": self.emp_b.pk, "consent_note": "x"}).status_code, 403)
            self.assertEqual(self.client.post(reverse("fingerprint-withdraw", args=[self.enrolment.pk]), {"reason": "x"}).status_code, 403)
        self.login(self.hr)
        created = self.client.post(reverse("fingerprint-enrolments"), {"employee": self.emp_b.pk, "consent_note": "Form 3"})
        self.assertEqual((created.status_code, created.data["device_user_id"]), (201, 2))
        withdrawn = self.client.post(reverse("fingerprint-withdraw", args=[created.data["id"]]), {"reason": "Left"})
        self.assertEqual((withdrawn.status_code, withdrawn.data["active"]), (200, False))
        self.assertEqual(len(self.client.get(reverse("fingerprint-enrolments"), {"active": "true"}).data), 1)

    def test_no_fingerprint_data_is_stored(self):
        stored = {f.name for model in (Enrolment, ClockDevice, Punch) for f in model._meta.fields}
        self.assertFalse({"template", "fingerprint", "biometric", "image"} & stored)


class PunchTests(FingerprintBase):
    def test_in_and_out_make_a_fingerprint_record_with_no_location_flags(self):
        first = self.punch("in", "09:02")
        self.assertEqual((first.outcome, first.reason), ("accepted", ""))
        record = self.record()
        self.assertEqual((first.record, first.employee, first.effective_at), (record, self.emp_a, at(0, "09:02")))
        self.assertEqual((record.source, record.late_minutes, record.flags, record.in_distance_m), ("fingerprint", 2, [], None))
        self.assertEqual(record.review_status, ReviewStatus.NOT_REQUIRED)
        self.punch("out", "17:00")
        record.refresh_from_db()
        self.assertEqual((record.worked_minutes, record.flags), (478, ["no_break"]))  # a note only: no break punches
        self.assertEqual(record.review_status, ReviewStatus.NOT_REQUIRED)

    def test_our_clock_is_used_when_the_device_is_a_few_minutes_out(self):
        punch = self.punch("in", "09:00", device_time=at(0, "09:04"))
        self.assertEqual(punch.effective_at, at(0, "09:00"))
        self.assertEqual(self.record().clock_in_at, at(0, "09:00"))
        self.assertEqual(self.record().flags, [])

    def test_a_late_arriving_punch_uses_the_device_time_and_asks_a_manager_to_confirm(self):
        punch = self.punch("in", "09:30", device_time=at(0, "09:00"))
        self.assertEqual(punch.effective_at, at(0, "09:00"))
        record = self.record()
        self.assertEqual((record.clock_in_at, record.flags, record.review_status), (at(0, "09:00"), ["delayed_punch"], ReviewStatus.PENDING))
        self.assertEqual(queued("attendance_review"), ["mgr-a@example.com"])

    def test_a_device_time_in_the_future_or_over_a_day_old_is_refused(self):
        future = self.punch("in", "09:00", device_time=at(0, "09:20"))
        self.assertEqual((future.outcome, future.reason), ("rejected", "device_clock_wrong"))
        old = self.punch("in", "09:00", device_time=at(0, "09:00") - timedelta(hours=25))
        self.assertEqual((old.outcome, old.reason), ("rejected", "device_clock_wrong"))
        self.assertEqual(AttendanceRecord.objects.count(), 0)

    def test_the_edges_of_the_clock_tolerance(self):
        self.assertEqual(self.punch("in", "09:00", device_time=at(0, "08:55")).effective_at, at(0, "09:00"))  # 5 min: ours
        services.process_punch(device=self.device, user_id=self.enrolment.device_user_id, kind="out",
                               device_time=at(0, "17:00") - timedelta(hours=24), received_at=at(0, "17:00"))
        late = self.punch("out", "17:00", device_time=at(0, "16:54"))  # 6 min late: device time, flagged
        self.assertEqual((late.effective_at, late.outcome), (at(0, "16:54"), "accepted"))
        self.assertIn("delayed_punch", self.flags())

    def test_sending_the_same_punch_twice_changes_nothing(self):
        first = self.punch("in", "09:00")
        again = self.punch("in", "09:00")
        self.assertEqual(first.pk, again.pk)
        self.assertEqual((AttendanceRecord.objects.count(), Punch.objects.count()), (1, 1))

    def test_a_double_tap_within_a_minute_is_ignored_but_a_later_repeat_is_refused(self):
        self.punch("in", "09:00")
        tap = services.process_punch(device=self.device, user_id=1, kind="in", device_time=at(0, "09:00") + timedelta(seconds=30),
                                     received_at=at(0, "09:00") + timedelta(seconds=30))
        self.assertEqual((tap.outcome, tap.reason), ("duplicate", "double_tap"))
        later = self.punch("in", "09:05")
        self.assertEqual((later.outcome, later.reason), ("rejected", "already_clocked_in"))
        self.assertEqual(AttendanceRecord.objects.count(), 1)

    def test_a_refused_punch_does_not_swallow_the_real_one_that_follows(self):
        too_early = at(0, "08:29") + timedelta(seconds=40)
        refused = self.punch("in", "08:29", device_time=too_early, received=too_early)
        self.assertEqual(refused.reason, "too_early")
        real = at(0, "08:30") + timedelta(seconds=20)  # 40 seconds later, now inside the 30-minute window
        accepted = self.punch("in", "08:30", device_time=real, received=real)
        self.assertEqual((accepted.outcome, accepted.reason), ("accepted", ""))
        self.assertEqual(AttendanceRecord.objects.count(), 1)

    def test_refused_punches_are_logged_with_the_reason(self):
        cases = [
            (self.punch("in", "07:00"), "too_early"),
            (self.punch("in", "10:00", n=3), "no_shift"),
            (self.punch("out", "10:00"), "not_clocked_in"),
        ]
        for punch, reason in cases:
            self.assertEqual((punch.outcome, punch.reason), ("rejected", reason))
            self.assertIsNone(punch.record)
        self.assertEqual(Punch.objects.count(), 3)
        self.assertEqual(AttendanceRecord.objects.count(), 0)

    def test_unknown_numbers_are_refused_and_withdrawn_people_too(self):
        unknown = services.process_punch(device=self.device, user_id=77, kind="in", device_time=at(0, "09:00"), received_at=at(0, "09:00"))
        self.assertEqual((unknown.outcome, unknown.reason, unknown.employee), ("rejected", "unknown_user", None))
        services.withdraw(enrolment=self.enrolment, by=self.hr, reason="Prefers the app")
        gone = self.punch("in", "09:00")
        self.assertEqual((gone.outcome, gone.reason), ("rejected", "withdrawn"))
        self.assertEqual(gone.employee, self.emp_a)
        self.assertEqual(AttendanceRecord.objects.count(), 0)

    def test_someone_who_no_longer_works_here_is_refused(self):
        self.emp_a.end_date = at(0, "09:00").date() - timedelta(days=1)
        self.emp_a.save()
        self.assertEqual(self.punch("in", "09:00").reason, "not_employed")

    def test_a_device_at_the_wrong_shop_is_flagged_for_the_manager(self):
        other, _ = services.register_device(store=self.store_b, name="Back", serial="ZK-0002")
        self.punch("in", "09:00", device=other)
        record = self.record()
        self.assertEqual((record.flags, record.review_status), (["wrong_store_device"], ReviewStatus.PENDING))
        self.assertEqual(record.store, self.store_a)  # the record stays with the shift's shop
        send_everything()
        self.assertIn("different shop", inbox("mgr-a@example.com")[0].body)

    def test_the_app_is_flagged_where_a_shop_has_a_device(self):
        record = self.clock_in(device="phone-1")
        self.assertEqual((record.flags, record.review_status), (["app_instead_of_device"], ReviewStatus.PENDING))
        out = self.clock_out(device="phone-1")
        self.assertIn("out:app_instead_of_device", out.flags)

    def test_the_app_is_not_flagged_at_a_shop_without_a_device(self):
        ClockDevice.objects.filter(store=self.store_a).update(is_active=False)
        self.assertEqual(self.clock_in().flags, [])

    def test_a_device_punch_after_an_app_clock_in_confirms_it_without_a_second_record(self):
        self.clock_in("09:00")
        punch = self.punch("in", "09:03")
        self.assertEqual((punch.outcome, punch.reason), ("duplicate", "confirms_app_clock_in"))
        self.assertEqual(AttendanceRecord.objects.count(), 1)
        self.assertNotIn("app_device_mismatch", self.flags())

    def test_the_app_and_the_device_disagreeing_by_more_than_five_minutes_is_flagged(self):
        self.clock_in("09:00")
        self.punch("in", "09:12")
        self.assertIn("app_device_mismatch", self.flags())
        self.assertEqual(self.record().review_status, ReviewStatus.PENDING)

    def test_a_device_punch_out_closes_an_app_clock_in(self):
        self.clock_in("09:00")
        self.punch("out", "17:00")
        self.assertEqual(self.record().worked_minutes, 480)


class BreakTests(FingerprintBase):
    def test_a_short_break_is_all_paid_and_the_unused_time_is_shown(self):
        record = self.day_of_breaks(20)
        info = breaks.summary(record, at(0, "18:00"))
        self.assertEqual((info["allowed"], info["taken"], info["paid"], info["unpaid"], info["unused"]), (30, 20, 20, 0, 10))
        self.assertEqual(record.flags, [])

    def test_time_over_the_allowance_is_unpaid(self):
        record = self.day_of_breaks(45)
        info = breaks.summary(record, at(0, "18:00"))
        self.assertEqual((info["allowed"], info["taken"], info["paid"], info["unpaid"]), (30, 45, 30, 15))
        self.assertEqual((record.flags, record.review_status), (["break_over"], ReviewStatus.NOT_REQUIRED))  # for the report, no review

    def test_no_break_on_a_shift_that_has_one_is_noted(self):
        record = self.day_of_breaks(0)
        self.assertEqual(record.flags, ["no_break"])
        self.assertEqual(breaks.summary(record, at(0, "18:00"))["unused"], 30)

    def test_a_shift_under_six_hours_has_no_break_and_taking_one_is_flagged(self):
        employee, enrolment = self.worker("short@example.com", "09:00", "14:00")
        self.punch("in", "09:00", enrolment)
        self.punch("break_start", "11:00", enrolment)
        self.punch("break_end", "11:15", enrolment)
        self.punch("out", "14:00", enrolment)
        record = self.record(employee)
        info = breaks.summary(record, at(0, "18:00"))
        self.assertEqual((info["allowed"], info["paid"], info["unpaid"]), (0, 0, 15))
        self.assertEqual((record.flags, record.review_status), (["break_not_allowed"], ReviewStatus.PENDING))

    def test_exactly_six_hours_gets_a_break(self):
        employee, enrolment = self.worker("six@example.com", "09:00", "15:00")
        for kind, when in [("in", "09:00"), ("break_start", "12:00"), ("break_end", "12:30"), ("out", "15:00")]:
            self.punch(kind, when, enrolment)
        record = self.record(employee)
        self.assertEqual((record.flags, breaks.summary(record, at(0, "18:00"))["paid"]), ([], 30))

    def test_a_shift_over_twelve_hours_gets_an_hour(self):
        employee, enrolment = self.worker("long@example.com", "08:00", "21:00")
        for kind, when in [("in", "08:00"), ("break_start", "13:00"), ("break_end", "14:00"), ("out", "21:00")]:
            self.punch(kind, when, enrolment)
        record = self.record(employee)
        info = breaks.summary(record, at(0, "22:00"))
        self.assertEqual((info["allowed"], info["paid"], info["unpaid"], record.flags), (60, 60, 0, []))

    def test_exactly_twelve_hours_gets_thirty_minutes(self):
        employee, enrolment = self.worker("twelve@example.com", "08:00", "20:00")
        for kind, when in [("in", "08:00"), ("break_start", "13:00"), ("break_end", "14:00"), ("out", "20:00")]:
            self.punch(kind, when, enrolment)
        info = breaks.summary(self.record(employee), at(0, "22:00"))
        self.assertEqual((info["allowed"], info["paid"], info["unpaid"]), (30, 30, 30))

    def test_several_breaks_add_up_against_the_one_allowance(self):
        self.punch("in", "09:00")
        for start, end in [("11:00", "11:20"), ("14:00", "14:20")]:
            self.punch("break_start", start)
            self.punch("break_end", end)
        self.punch("out", "17:00")
        info = breaks.summary(self.record(), at(0, "18:00"))
        self.assertEqual((info["taken"], info["paid"], info["unpaid"]), (40, 30, 10))
        self.assertEqual(BreakPeriod.objects.count(), 2)

    def test_a_break_that_is_never_ended_is_closed_at_clock_out_and_a_manager_is_asked(self):
        self.punch("in", "09:00")
        self.punch("break_start", "16:00")
        self.punch("out", "17:00")
        record = self.record()
        self.assertEqual(BreakPeriod.objects.get().ended_at, at(0, "17:00"))
        self.assertIn("break_not_ended", record.flags)
        self.assertEqual(record.review_status, ReviewStatus.PENDING)
        self.assertEqual(breaks.summary(record, at(0, "18:00"))["unpaid"], 30)

    def test_the_break_punches_must_be_in_order(self):
        self.assertEqual(self.punch("break_start", "11:00").reason, "not_clocked_in")
        self.punch("in", "09:00")
        self.assertEqual(self.punch("break_end", "12:30").reason, "not_on_break")
        self.punch("break_start", "12:00")
        self.assertEqual(self.punch("break_start", "12:10").reason, "already_on_break")
        self.assertEqual(self.punch("break_end", "11:00").reason, "bad_order")
        self.assertEqual(self.punch("break_end", "12:31").outcome, "accepted")

    def test_a_break_cannot_start_before_the_clock_in(self):
        self.punch("in", "09:00")
        early = services.process_punch(
            device=self.device, user_id=1, kind="break_start", device_time=at(0, "08:30"), received_at=at(0, "09:01"),
        )
        self.assertEqual((early.outcome, early.reason), ("rejected", "bad_order"))

    def test_a_late_arriving_break_punch_is_flagged(self):
        self.punch("in", "09:00")
        self.punch("break_start", "12:40", device_time=at(0, "12:00"))
        self.assertIn("delayed_punch", self.flags())

    def test_when_the_rota_and_the_hours_worked_disagree_a_manager_decides(self):
        # rostered 09:00-14:00 (no break) but stayed until 15:30 and took 20 minutes
        employee, enrolment = self.worker("stayed@example.com", "09:00", "14:00")
        for kind, when in [("in", "09:00"), ("break_start", "12:00"), ("break_end", "12:20"), ("out", "15:30")]:
            self.punch(kind, when, enrolment)
        record = self.record(employee)
        self.assertEqual((record.flags, record.review_status), (["break_hours_differ"], ReviewStatus.PENDING))
        info = breaks.summary(record, at(0, "18:00"))
        self.assertEqual((info["basis"], info["paid"], info["unpaid"]), ("actual", 20, 0))  # hours worked, until a decision
        self.login(self.manager_a)
        response = self.client.post(reverse("break-basis", args=[record.pk]), {"basis": "rota", "reason": "Only asked to cover until 2"})
        self.assertEqual((response.status_code, response.data["break_basis"]), (200, "rota"))
        record.refresh_from_db()
        info = breaks.summary(record, at(0, "18:00"))
        self.assertEqual((info["basis"], info["allowed"], info["paid"], info["unpaid"]), ("rota", 0, 0, 20))
        edit = AttendanceEdit.objects.get(record=record)
        self.assertEqual((edit.changes, edit.reason), ({"break_basis": ["actual", "rota"]}, "Only asked to cover until 2"))

    def test_leaving_early_after_a_break_is_flagged_both_ways(self):
        # rostered 09:00-17:00 (30 minutes) but left at 14:00 (5 hours: no break) after taking 20
        for kind, when in [("in", "09:00"), ("break_start", "11:00"), ("break_end", "11:20"), ("out", "14:00")]:
            self.punch(kind, when)
        record = self.record()
        self.assertEqual(set(record.flags) - {"left_early"}, {"break_not_allowed", "break_hours_differ"})
        info = breaks.summary(record, at(0, "18:00"))
        self.assertEqual((info["rota_allowed"], info["actual_allowed"], info["unpaid"]), (30, 0, 20))

    def test_breaks_are_judged_even_if_the_punching_device_is_at_another_shop(self):
        ClockDevice.objects.filter(store=self.store_a).update(is_active=False)
        other, _ = services.register_device(store=self.store_b, name="Back", serial="ZK-0002")
        employee, enrolment = self.worker("short@example.com", "09:00", "14:00")
        for kind, when in [("in", "09:00"), ("break_start", "11:00"), ("break_end", "11:15"), ("out", "14:00")]:
            self.punch(kind, when, enrolment, device=other)
        self.assertIn("break_not_allowed", self.flags(employee))

    def test_breaks_are_not_tracked_at_a_shop_with_no_device_and_no_break_punches(self):
        ClockDevice.objects.filter(store=self.store_a).update(is_active=False)
        self.clock_in("09:00")
        self.clock_out("17:00")
        self.assertEqual(self.flags(), [])

    def test_the_basis_can_only_be_set_by_the_right_manager_with_a_reason(self):
        record = self.day_of_breaks(20)
        url = reverse("break-basis", args=[record.pk])
        self.login(self.manager_b)
        self.assertEqual(self.client.post(url, {"basis": "rota", "reason": "x"}).status_code, 404)
        self.login(self.emp_a.user)
        self.assertEqual(self.client.post(url, {"basis": "rota", "reason": "x"}).status_code, 403)
        self.login(self.manager_a)
        self.assertEqual(self.client.post(url, {"basis": "rota", "reason": " "}).status_code, 400)
        self.assertEqual(self.client.post(url, {"basis": "other", "reason": "x"}).status_code, 400)
        self.assertEqual(self.client.post(url, {"basis": "actual", "reason": "x"}).status_code, 400)  # already the default
        self.assertEqual(self.client.post(url, {"basis": "rota", "reason": "x"}).status_code, 200)
        self.assertEqual(self.client.post(url, {"basis": "rota", "reason": "x"}).status_code, 400)


class ReportTests(FingerprintBase):
    def setUp(self):
        super().setUp()
        self.ben, self.ben_enrolment = self.worker("ben@example.com", "09:00", "17:00")
        self.cat, self.cat_enrolment = self.worker("cat@example.com", "09:00", "17:00")
        self.day_of_breaks(20)  # emp_a: all paid
        for kind, when in [("in", "09:00"), ("break_start", "12:00"), ("break_end", "12:55"), ("out", "17:00")]:
            self.punch(kind, when, self.ben_enrolment)  # ben: 55 minutes, 25 unpaid
        self.punch("in", "09:00", self.cat_enrolment)  # cat: still clocked in
        self.report = reverse("break-report")
        self.range = {"start": at(0, "09:00").date().isoformat(), "end": at(0, "09:00").date().isoformat()}

    def test_the_report_ranks_people_by_unpaid_break_and_leaves_out_open_shifts(self):
        self.login(self.manager_a)
        data = self.client.get(self.report, self.range).data
        people = [(p["employee"]["employee_number"], p["taken"], p["paid"], p["unpaid"], p["over_count"]) for p in data["people"]]
        self.assertEqual(people, [(self.ben.employee_number, 55, 30, 25, 1), (self.emp_a.employee_number, 20, 20, 0, 0)])
        self.assertEqual(len(data["shifts"]), 2)  # cat is still working
        first = data["shifts"][1]
        self.assertEqual((first["allowed"], first["taken"], first["unpaid"], first["basis"]), (30, 55, 25, "actual"))
        self.assertEqual(data["people"][0]["average_taken"], 55.0)
        self.assertEqual([p["longest"] for p in data["people"]], [55, 20])

    def test_a_persons_average_and_longest_cover_all_their_shifts(self):
        self.shift(self.ben, 1, "09:00", "17:00")
        for kind, when in [("in", "09:00"), ("break_start", "12:00"), ("break_end", "12:25"), ("out", "17:00")]:
            self.punch(kind, when, self.ben_enrolment, n=1)
        self.login(self.manager_a)
        data = self.client.get(self.report, {"start": at(0, "09:00").date().isoformat(), "end": at(1, "09:00").date().isoformat()}).data
        ben = [p for p in data["people"] if p["employee"]["id"] == self.ben.pk][0]
        self.assertEqual(
            (ben["shifts"], ben["taken"], ben["average_taken"], ben["longest"], ben["unpaid"], ben["over_count"]),
            (2, 80, 40.0, 55, 25, 1),
        )

    def test_it_counts_people_who_took_no_break(self):
        self.punch("out", "17:00", self.cat_enrolment)
        self.login(self.manager_a)
        data = self.client.get(self.report, self.range).data
        cat = [p for p in data["people"] if p["employee"]["id"] == self.cat.pk][0]
        self.assertEqual((cat["taken"], cat["unused"], cat["no_break_count"]), (0, 30, 1))

    def test_a_managers_decision_changes_what_the_report_says(self):
        dee, dee_enrolment = self.worker("dee@example.com", "09:00", "14:00")  # rostered 5 hours: no break
        for kind, when in [("in", "09:00"), ("break_start", "12:00"), ("break_end", "12:20"), ("out", "15:30")]:
            self.punch(kind, when, dee_enrolment)  # stayed 6.5 hours and took 20 minutes
        self.login(self.manager_a)
        before = [p for p in self.client.get(self.report, self.range).data["people"] if p["employee"]["id"] == dee.pk][0]
        self.assertEqual((before["paid"], before["unpaid"]), (20, 0))
        services.set_break_basis(record=self.record(dee), by=self.manager_a, basis="rota", reason="Only asked to cover until 2")
        after = [p for p in self.client.get(self.report, self.range).data["people"] if p["employee"]["id"] == dee.pk][0]
        self.assertEqual((after["paid"], after["unpaid"]), (0, 20))

    def test_a_manager_sees_only_their_own_shop_and_employees_see_nothing(self):
        self.login(self.manager_b)
        data = self.client.get(self.report, self.range).data
        self.assertEqual((data["people"], data["shifts"]), ([], []))
        self.login(self.owner)
        self.assertEqual(len(self.client.get(self.report, self.range).data["people"]), 2)
        self.assertEqual(len(self.client.get(self.report, {**self.range, "store": self.store_b.pk}).data["people"]), 0)
        self.assertEqual(len(self.client.get(self.report, {**self.range, "employee": self.ben.pk}).data["people"]), 1)
        self.login(self.emp_a.user)
        self.assertEqual(self.client.get(self.report, self.range).status_code, 403)

    def test_the_date_range_is_checked(self):
        self.login(self.manager_a)
        self.assertEqual(self.client.get(self.report, {"start": "2026-01-01", "end": "2026-06-01"}).status_code, 400)
        self.assertEqual(self.client.get(self.report, {"start": "2026-06-01", "end": "2026-01-01"}).status_code, 400)
        self.assertEqual(self.client.get(self.report, {"start": "2026-01-01", "end": "2026-03-03"}).status_code, 200)  # 62 days counting both ends
        self.assertEqual(self.client.get(self.report, {"start": "2026-01-01", "end": "2026-03-04"}).status_code, 400)  # 63 days

    def test_the_punch_log_shows_refused_punches_with_a_reason_to_the_right_manager(self):
        self.punch("out", "10:00", n=3)  # not rostered
        self.login(self.manager_a)
        rejected = self.client.get(reverse("clock-punches"), {**self.range, "end": at(3, "10:00").date().isoformat(), "outcome": "rejected"}).data
        self.assertEqual([(row["kind"], row["reason"]) for row in rejected], [("out", "not_clocked_in")])
        self.assertEqual(rejected[0]["message"], "Not clocked in.")
        every = self.client.get(reverse("clock-punches"), {**self.range, "end": at(3, "10:00").date().isoformat()}).data
        self.assertGreater(len(every), len(rejected))
        self.login(self.manager_b)
        self.assertEqual(self.client.get(reverse("clock-punches"), {**self.range, "end": at(3, "10:00").date().isoformat()}).data, [])
        self.login(self.emp_a.user)
        self.assertEqual(self.client.get(reverse("clock-punches")).status_code, 403)


class AlertTests(FingerprintBase):
    def test_a_flagged_device_punch_emails_only_that_shops_managers_in_plain_words(self):
        self.punch("in", "09:30", device_time=at(0, "09:00"))
        send_everything()
        self.assertEqual(inbox("mgr-b@example.com"), [])
        body = inbox("mgr-a@example.com")[0].body
        self.assertIn("sent this late", body)
        self.assertNotIn("fingerprint data", body.lower())

    def test_break_flags_that_need_a_decision_email_the_managers_but_over_break_alone_does_not(self):
        self.day_of_breaks(45)  # over the allowance: for the report only
        self.assertEqual(queued("attendance_review"), [])
        employee, enrolment = self.worker("short@example.com", "09:00", "14:00")
        for kind, when in [("in", "09:00"), ("break_start", "11:00"), ("break_end", "11:15"), ("out", "14:00")]:
            self.punch(kind, when, enrolment)
        self.assertEqual(queued("attendance_review"), ["mgr-a@example.com"])
        send_everything()
        self.assertIn("under 6 hours", inbox("mgr-a@example.com")[0].body)
