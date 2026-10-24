from datetime import timedelta

from django.core.management import call_command
from io import StringIO

from rest_framework.exceptions import ValidationError

from apps.rota import services as rota_services
from apps.rota.models import ShiftStatus

from . import services
from .models import AttendanceEdit, AttendanceRecord, ReviewStatus, Source
from .tests_base import FAR, NEAR, AttendanceBase, at, day


class ClockInWindowTests(AttendanceBase):
    def assertRefused(self, code, **kwargs):
        with self.assertRaises(services.AttendanceError) as caught:
            self.clock_in(**kwargs)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_clock_in_on_time_inside_the_shop_is_clean(self):
        record = self.clock_in("09:00")
        self.assertEqual(record.flags, [])
        self.assertEqual(record.review_status, ReviewStatus.NOT_REQUIRED)
        self.assertEqual((record.late_minutes, record.shift, record.store), (0, self.shift_a, self.store_a))
        self.assertLess(record.in_distance_m, 100)
        self.assertEqual(record.source, Source.DEVICE)

    def test_lateness_is_worked_out_from_the_rota(self):
        self.assertEqual(self.clock_in("09:20").late_minutes, 20)

    def test_up_to_thirty_minutes_early_is_allowed_but_not_more(self):
        self.assertRefused("too_early", when="08:29")
        self.assertIn("09:00", self.assertRefused("too_early", when="08:29").message)
        self.assertEqual(self.clock_in("08:30").late_minutes, 0)

    def test_you_can_clock_in_late_until_the_shift_ends_but_not_after(self):
        self.assertEqual(self.clock_in("16:59").late_minutes, 479)
        self.clock_out("17:00")
        self.assertRefused("no_shift", when="17:01", employee=self.emp_a, n=0)

    def test_no_rostered_shift_means_no_clocking_in(self):
        self.assertRefused("no_shift", n=2)  # nothing on day 2
        self.assertRefused("no_shift", employee=self.emp_b)  # someone else's shop and rota

    def test_cancelled_and_unpublished_shifts_do_not_count(self):
        draft_week = rota_services.create_week(store=self.store_a, week_start=day(7), by=self.hr)[0]
        self.shift(self.emp_a, 7, "09:00", "17:00", week=draft_week)
        self.assertRefused("no_shift", n=7)
        self.shift_a.status = ShiftStatus.CANCELLED
        self.shift_a.save()
        self.assertRefused("no_shift")

    def test_cannot_clock_in_twice_or_twice_for_one_shift(self):
        self.clock_in("09:00")
        self.assertRefused("already_clocked_in", when="09:05")
        self.clock_out("12:00")
        self.assertRefused("already_worked", when="12:30")

    def test_split_shifts_can_each_be_clocked(self):
        self.shift_a.end_time = at(0, "13:00").time()
        self.shift_a.save()
        second = self.shift(self.emp_a, 0, "14:00", "18:00")
        first_record = self.clock_in("09:00")
        self.clock_out("13:00")
        second_record = self.clock_in("13:40")
        self.assertEqual((first_record.shift, second_record.shift), (self.shift_a, second))

    def test_overnight_shift_can_be_clocked_after_midnight(self):
        night = self.shift(self.emp_a, 2, "22:00", "06:00")
        self.assertEqual(self.clock_in("22:00", n=2).shift, night)
        self.clock_out("06:00", n=3)
        again = self.shift(self.emp_a, 4, "23:00", "07:00")
        self.assertEqual(again.pk, self.clock_in("23:30", n=4).shift_id)

    def test_after_midnight_the_overnight_shift_from_yesterday_is_found(self):
        night = self.shift(self.emp_a, 2, "22:00", "06:00")
        self.assertEqual(self.clock_in("02:00", n=3).shift, night)

    def test_people_who_have_left_or_are_deactivated_cannot_clock(self):
        self.emp_a.end_date = day(0) - timedelta(days=1)
        self.emp_a.save()
        self.assertEqual(self.assertRefused("not_employed").status_code, 403)
        self.emp_a.end_date = None
        self.emp_a.save()
        self.emp_a.user.is_active = False
        self.emp_a.user.save()
        self.assertRefused("not_employed")


class LocationChecksTests(AttendanceBase):
    def test_far_from_the_shop_is_saved_but_flagged_for_review(self):
        record = self.clock_in(where=FAR)
        self.assertEqual(record.flags, ["out_of_area"])
        self.assertEqual(record.review_status, ReviewStatus.PENDING)
        self.assertGreater(record.in_distance_m, 400)

    def test_accuracy_over_100_metres_cannot_be_trusted(self):
        for accuracy, expected in ((100, []), (101, ["poor_accuracy"]), (None, ["poor_accuracy"]), (900, ["poor_accuracy"])):
            with self.subTest(accuracy=accuracy):
                AttendanceRecord.objects.all().delete()
                self.assertEqual(self.clock_in(accuracy=accuracy).flags, expected)

    def test_sharing_no_location_is_allowed_but_flagged(self):
        record = self.clock_in(where=None)
        self.assertEqual((record.flags, record.in_distance_m), (["no_location"], None))
        self.assertEqual(record.review_status, ReviewStatus.PENDING)

    def test_impossible_coordinates_are_refused(self):
        for where in ((91, 0), (0, 181), (-91, 0)):
            with self.subTest(where=where), self.assertRaises(services.AttendanceError):
                self.clock_in(where=where)
        with self.assertRaises(services.AttendanceError):
            self.clock_in(accuracy=-1)

    def test_a_shop_with_no_location_set_up_cannot_be_checked_so_nothing_is_flagged(self):
        self.store_a.latitude = self.store_a.longitude = None
        self.store_a.save()
        record = self.clock_in(where=FAR)
        self.assertEqual((record.flags, record.in_distance_m), ([], None))

    def test_the_shops_own_radius_is_used(self):
        self.store_a.radius_m = 1000
        self.store_a.save()
        self.assertEqual(self.clock_in(where=FAR).flags, [])

    def test_a_device_used_by_two_people_is_flagged(self):
        other = self.hire("emp-d@example.com", self.store_a)
        self.clear(other)
        self.shift(other, 0, "09:00", "17:00")
        self.clock_in("09:00", device="phone-1")
        second = self.clock_in("09:05", device="phone-1", employee=other)
        self.assertEqual(second.flags, ["shared_device"])
        self.assertEqual(second.review_status, ReviewStatus.PENDING)

    def test_device_sharing_is_only_checked_for_twelve_hours_and_between_different_people(self):
        other = self.hire("emp-e@example.com", self.store_a)
        self.clear(other)
        self.shift(other, 1, "09:00", "17:00")
        self.clock_in("09:00", device="phone-2")
        self.clock_out("17:00", device="phone-2")  # same person on the same phone: fine
        self.assertEqual(AttendanceRecord.objects.get().flags, [])
        later = self.clock_in("09:00", device="phone-2", employee=other, n=1)  # next day, over 12 h later
        self.assertEqual(later.flags, [])


class ClockOutTests(AttendanceBase):
    def test_clock_out_closes_the_record(self):
        self.clock_in("09:00")
        record = self.clock_out("17:00")
        self.assertEqual(record.worked_minutes, 480)
        self.assertEqual((record.flags, record.review_status), ([], ReviewStatus.NOT_REQUIRED))

    def test_cannot_clock_out_when_not_clocked_in(self):
        with self.assertRaises(services.AttendanceError) as caught:
            self.clock_out()
        self.assertEqual((caught.exception.code, caught.exception.status_code), ("not_clocked_in", 409))

    def test_a_double_tap_is_refused(self):
        self.clock_in("09:00")
        with self.assertRaises(services.AttendanceError) as caught:
            services.clock_out(employee=self.emp_a, now=at(0, "09:00") + timedelta(seconds=20))
        self.assertEqual(caught.exception.code, "too_soon")

    def test_clock_out_location_problems_are_flagged_with_an_out_prefix(self):
        self.clock_in("09:00")
        record = self.clock_out("17:00", where=FAR)
        self.assertEqual(record.flags, ["out:out_of_area"])
        self.assertEqual(record.review_status, ReviewStatus.PENDING)

    def test_a_clock_in_flag_stays_on_the_record_after_a_clean_clock_out(self):
        self.clock_in("09:00", where=FAR)
        record = self.clock_out("17:00")
        self.assertEqual(record.flags, ["out_of_area"])

    def test_forgetting_to_clock_out_is_flagged(self):
        self.clock_in("09:00")
        record = self.clock_out("21:30")
        self.assertIn("late_clock_out", record.flags)
        self.assertEqual(record.review_status, ReviewStatus.PENDING)

    def test_leaving_early_is_noted_but_does_not_need_a_review(self):
        self.clock_in("09:00")
        record = self.clock_out("15:00")
        self.assertEqual(record.flags, ["left_early"])
        self.assertEqual(record.review_status, ReviewStatus.NOT_REQUIRED)

    def test_a_shared_device_is_also_caught_at_clock_out(self):
        other = self.hire("emp-f@example.com", self.store_a)
        self.clear(other)
        self.shift(other, 0, "09:00", "17:00")
        self.clock_in("09:00", device="phone-3")
        self.clock_in("09:01", device="phone-4", employee=other)
        self.assertEqual(AttendanceRecord.objects.get(employee=self.emp_a).flags, [])
        record = self.clock_out("17:00", device="phone-4")  # the phone the other person clocked in with
        self.assertEqual(record.flags, ["out:shared_device"])
        self.assertEqual(record.review_status, ReviewStatus.PENDING)


class ManagerDecisionTests(AttendanceBase):
    def flagged(self):
        record = self.clock_in(where=FAR)
        self.clock_out("17:00")
        return AttendanceRecord.objects.get(pk=record.pk)

    def test_approve_and_reject(self):
        approved = services.review_record(record=self.flagged(), by=self.manager_a, approve=True)
        self.assertEqual((approved.review_status, approved.reviewed_by), (ReviewStatus.APPROVED, self.manager_a))
        AttendanceRecord.objects.all().delete()
        rejected = services.review_record(record=self.flagged(), by=self.manager_a, approve=False, note="Not at work")
        self.assertEqual((rejected.review_status, rejected.review_note), (ReviewStatus.REJECTED, "Not at work"))

    def test_rejecting_needs_a_reason_and_only_waiting_entries_can_be_decided(self):
        record = self.flagged()
        with self.assertRaises(ValidationError):
            services.review_record(record=record, by=self.manager_a, approve=False, note="  ")
        services.review_record(record=record, by=self.manager_a, approve=True)
        with self.assertRaises(ValidationError):
            services.review_record(record=record, by=self.manager_a, approve=True)


class EditTests(AttendanceBase):
    def closed(self):
        self.clock_in("09:00")
        return self.clock_out("17:00")

    def edit(self, record, **kwargs):
        kwargs.setdefault("reason", "Phone battery died")
        return services.edit_record(record=record, by=self.manager_a, now=at(1, "12:00"), **kwargs)

    def test_edit_changes_the_time_and_writes_an_audit_row(self):
        record = self.closed()
        edited = self.edit(record, clock_out_at=at(0, "17:30"))
        self.assertEqual(edited.worked_minutes, 510)
        self.assertIn("manually_edited", edited.flags)
        entry = AttendanceEdit.objects.get()
        self.assertEqual((entry.edited_by, entry.reason), (self.manager_a, "Phone battery died"))
        self.assertEqual(set(entry.changes), {"clock_out_at"})
        self.assertEqual(len(entry.changes["clock_out_at"]), 2)

    def test_forgotten_clock_out_can_be_filled_in(self):
        self.clock_in("09:00")
        record = AttendanceRecord.objects.get()
        self.assertEqual(self.edit(record, clock_out_at=at(0, "17:00")).worked_minutes, 480)

    def test_a_reason_is_required_and_a_change_must_be_a_change(self):
        record = self.closed()
        with self.assertRaises(ValidationError):
            self.edit(record, reason=" ", clock_out_at=at(0, "17:30"))
        with self.assertRaises(ValidationError):
            self.edit(record, clock_out_at=record.clock_out_at)
        self.assertEqual(AttendanceEdit.objects.count(), 0)

    def test_bad_times_are_refused(self):
        record = self.closed()
        for change in (
            {"clock_out_at": at(0, "08:00")},            # before the clock-in
            {"clock_out_at": at(5, "10:00")},            # in the future
            {"clock_in_at": at(0, "09:00") - timedelta(hours=21)},  # over 20 hours
        ):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                self.edit(record, **change)
        self.assertEqual(AttendanceEdit.objects.count(), 0)

    def test_a_time_in_the_future_is_refused_even_when_the_length_is_fine(self):
        self.shift(self.emp_a, 1, "09:00", "17:00")
        self.clock_in("09:00", n=1)
        record = self.clock_out("11:00", n=1)
        # "now" is 12:00 on day 1, so 13:00 has not happened yet
        with self.assertRaises(ValidationError):
            self.edit(record, clock_out_at=at(1, "13:00"))
        self.assertEqual(self.edit(record, clock_out_at=at(1, "11:59")).worked_minutes, 179)

    def test_an_edit_cannot_overlap_another_record(self):
        first = self.closed()
        self.shift(self.emp_a, 1, "09:00", "17:00")
        second = self.clock_in("09:00", n=1)
        self.clock_out("17:00", n=1)
        with self.assertRaises(ValidationError):
            self.edit(AttendanceRecord.objects.get(pk=second.pk), clock_in_at=at(0, "12:00"))
        self.assertEqual(first.pk, AttendanceRecord.objects.get(pk=first.pk).pk)


class ManualRecordTests(AttendanceBase):
    def add(self, **kwargs):
        values = dict(
            by=self.manager_a, employee=self.emp_a, store=self.store_a, clock_in_at=at(0, "09:00"),
            clock_out_at=at(0, "17:00"), reason="Forgot to clock in", now=at(1, "12:00"),
        )
        values.update(kwargs)
        return services.add_manual_record(**values)

    def test_a_missed_day_can_be_added_with_a_reason(self):
        record = self.add(shift=self.shift_a)
        self.assertEqual((record.source, record.flags, record.shift), (Source.MANUAL, ["manual_entry"], self.shift_a))
        self.assertEqual(AttendanceEdit.objects.get().reason, "Forgot to clock in")

    def test_manual_records_cannot_be_in_the_future(self):
        with self.assertRaises(ValidationError):
            self.add(clock_in_at=at(1, "11:00"), clock_out_at=at(1, "13:00"))

    def test_reason_store_shift_and_overlap_are_checked(self):
        with self.assertRaises(ValidationError):
            self.add(reason="")
        with self.assertRaises(ValidationError):
            self.add(store=self.store_b)  # not assigned there
        with self.assertRaises(ValidationError):
            self.add(shift=self.shift(self.emp_b, 0, "09:00", "17:00", week=self.week_b))
        self.add()
        with self.assertRaises(ValidationError):
            self.add(clock_in_at=at(0, "16:00"), clock_out_at=at(0, "18:00"))
        self.assertEqual(AttendanceRecord.objects.count(), 1)


class PurgeTests(AttendanceBase):
    def test_old_coordinates_are_cleared_but_the_evidence_stays(self):
        record = self.clock_in(where=FAR)
        self.clock_out("17:00", where=FAR)
        cleared = services.purge_locations(90, now=at(0, "12:00") + timedelta(days=91))
        record.refresh_from_db()
        self.assertEqual(cleared, 1)
        self.assertEqual((record.in_latitude, record.in_longitude, record.out_latitude), (None, None, None))
        self.assertIn("out_of_area", record.flags)
        self.assertGreater(record.in_distance_m, 400)

    def test_recent_records_are_left_alone_and_the_command_works(self):
        record = self.clock_in()
        self.assertEqual(services.purge_locations(90, now=at(0, "12:00") + timedelta(days=30)), 0)
        record.refresh_from_db()
        self.assertIsNotNone(record.in_latitude)
        out = StringIO()
        call_command("purge_attendance_locations", "--days", "90", stdout=out)
        self.assertIn("Cleared coordinates on", out.getvalue())
