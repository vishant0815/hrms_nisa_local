import json
from datetime import timedelta
from unittest import mock

from django.urls import reverse
from django.utils import timezone as dj_timezone
from rest_framework.test import APITestCase

from apps.accounts.models import Role
from apps.core.testutils import days_from_today, make_user
from apps.employees import services as employee_services
from apps.stores.models import Store, StoreHours
from apps.visa import services as visa_services
from apps.visa.models import EmployeeVisa, VacationPeriod, VisaType

from . import services
from .models import RotaWeek, Shift, ShiftStatus, ShiftTemplate, WeekStatus

TODAY = days_from_today(0)
NEXT_MON = TODAY + timedelta(days=7 - TODAY.weekday())  # a Monday, 1 to 7 days ahead


def day(n):
    return NEXT_MON + timedelta(days=n)


def pretend_today(when):
    """Make the rota services believe today is `when`, without touching anything else."""
    fake = mock.MagicMock()
    fake.localdate.return_value = when
    fake.now.side_effect = dj_timezone.now
    return mock.patch("apps.rota.services.timezone", fake)


class RotaTestBase(APITestCase):
    def setUp(self):
        self.owner = make_user("owner@example.com", Role.OWNER)
        self.hr = make_user("hr@example.com", Role.HR)
        self.manager_a = make_user("mgr-a@example.com", Role.MANAGER)
        self.manager_b = make_user("mgr-b@example.com", Role.MANAGER)
        self.store_a = Store.objects.create(name="Shop A")
        self.store_b = Store.objects.create(name="Shop B")
        self.store_a.managers.add(self.manager_a)
        self.store_b.managers.add(self.manager_b)
        self.emp_a = self.hire("emp-a@example.com", self.store_a)
        self.emp_b = self.hire("emp-b@example.com", self.store_b)
        self.clear(self.emp_a)
        self.clear(self.emp_b)
        self.week_a = services.create_week(store=self.store_a, week_start=NEXT_MON, by=self.hr)[0]
        self.week_b = services.create_week(store=self.store_b, week_start=NEXT_MON, by=self.hr)[0]

    # --- fixtures ---

    def hire(self, email, store, start=-60):
        return employee_services.create_employee(
            user=make_user(email, Role.EMPLOYEE),
            home_store=store,
            start_date=days_from_today(start),
            created_by=self.hr,
        )

    def clear(self, employee, type_name="British citizen", expiry=None):
        visa = visa_services.add_visa(
            employee=employee, by=self.hr, visa_type=VisaType.objects.get(name=type_name),
            start_date=None, expiry_date=expiry,
        )
        visa_services.record_check(
            employee=employee, by=self.hr, visa=visa, check_type="online", outcome="pass",
            checked_on=days_from_today(-5),
        )
        return visa

    def cover(self, employee, store, first=0, last=6):
        return employee_services.request_cover(
            employee=employee, store=store, valid_from=day(first), valid_to=day(last),
            reason="", requested_by=self.hr,
        )

    def login(self, user):
        self.client.force_authenticate(user)

    # --- API helpers ---

    def add(self, user, week, employee=None, n=0, start="09:00", end="17:00", **extra):
        self.login(user)
        body = {"date": day(n).isoformat(), "start_time": start, "end_time": end}
        if employee is not None:
            body["employee"] = employee.pk
        body.update(extra)
        return self.client.post(reverse("rota-week-shifts", args=[week.pk]), body)

    def codes(self, response):
        return [block["code"] for block in response.data["blocks"]]

    def warning_codes(self, response):
        return [warning["code"] for warning in response.data["warnings"]]

    def make_student(self, email="student@example.com"):
        student = self.hire(email, self.store_a)
        self.clear(student, "Student", expiry=day(300))
        return student


class ShiftBasicsTests(RotaTestBase):
    def test_manager_builds_a_shift_for_their_store(self):
        response = self.add(self.manager_a, self.week_a, self.emp_a, role="Cashier", notes="Training day")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["hours"], "8.00")
        self.assertEqual(response.data["store"]["name"], "Shop A")
        self.assertEqual(response.data["employee"]["id"], self.emp_a.pk)
        self.assertEqual(response.data["start_time"], "09:00:00")
        self.assertEqual(response.data["role"], "Cashier")
        self.assertEqual(response.data["status"], "scheduled")
        self.assertEqual(response.data["warnings"], [])

    def test_breaks_are_not_counted_as_working_hours(self):
        response = self.add(self.manager_a, self.week_a, self.emp_a, end="17:30", break_minutes=30)
        self.assertEqual(response.data["hours"], "8.00")

    def test_overnight_shift_runs_into_the_next_day(self):
        response = self.add(self.manager_a, self.week_a, self.emp_a, start="22:00", end="06:00")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["hours"], "8.00")

    def test_a_shift_with_no_one_on_it_is_an_open_shift(self):
        response = self.add(self.manager_a, self.week_a)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIsNone(response.data["employee"])

    def test_template_fills_in_the_times_and_can_be_overridden(self):
        template = ShiftTemplate.objects.create(
            store=self.store_a, name="Opening", start_time="07:00", end_time="15:00", break_minutes=30
        )
        self.login(self.manager_a)
        url = reverse("rota-week-shifts", args=[self.week_a.pk])
        plain = self.client.post(url, {"date": day(0).isoformat(), "template": template.pk})
        self.assertEqual(plain.status_code, 201, plain.data)
        self.assertEqual((plain.data["start_time"], plain.data["hours"]), ("07:00:00", "7.50"))
        changed = self.client.post(
            url, {"date": day(1).isoformat(), "template": template.pk, "end_time": "14:00"}
        )
        self.assertEqual((changed.data["end_time"], changed.data["hours"]), ("14:00:00", "6.50"))

    def test_templates_must_belong_to_the_store_and_be_active(self):
        other = ShiftTemplate.objects.create(store=self.store_b, name="Late", start_time="14:00", end_time="22:00")
        off = ShiftTemplate.objects.create(
            store=self.store_a, name="Old", start_time="08:00", end_time="16:00", is_active=False
        )
        self.login(self.manager_a)
        url = reverse("rota-week-shifts", args=[self.week_a.pk])
        for template in (other, off, 99999):
            with self.subTest(template=getattr(template, "name", template)):
                body = {"date": day(0).isoformat(), "template": getattr(template, "pk", template)}
                self.assertEqual(self.client.post(url, body).status_code, 400)

    def test_times_are_needed_without_a_template(self):
        self.login(self.manager_a)
        response = self.client.post(
            reverse("rota-week-shifts", args=[self.week_a.pk]), {"date": day(0).isoformat()}
        )
        self.assertEqual(response.status_code, 400)

    def test_malformed_shifts_are_refused(self):
        cases = {
            "date_outside_week": dict(n=8),
            "no_length": dict(start="09:00", end="09:00"),
            "shift_too_long": dict(start="06:00", end="20:00"),
            "break_too_long": dict(start="09:00", end="10:00", break_minutes=60),
        }
        for code, kwargs in cases.items():
            with self.subTest(code):
                response = self.add(self.manager_a, self.week_a, self.emp_a, **kwargs)
                self.assertEqual(response.status_code, 400)
                self.assertIn(code, self.codes(response))
        self.assertEqual(Shift.objects.count(), 0)

    def test_thirteen_hours_is_the_longest_shift(self):
        self.assertEqual(self.add(self.manager_a, self.week_a, start="07:00", end="20:00").status_code, 201)

    def test_shifts_in_the_past_cannot_be_created(self):
        with pretend_today(day(2)):
            response = self.add(self.manager_a, self.week_a, self.emp_a, n=0)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(self.codes(response), ["shift_in_past"])
            self.assertEqual(self.add(self.manager_a, self.week_a, self.emp_a, n=2).status_code, 201)


class AssignmentBlockTests(RotaTestBase):
    def test_someone_not_cleared_to_work_is_refused_without_saying_why(self):
        newcomer = self.hire("new@example.com", self.store_a)  # no right-to-work record
        response = self.add(self.manager_a, self.week_a, newcomer)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.codes(response), ["not_cleared"])
        self.assertEqual(response.data["blocks"][0]["message"], "Not cleared to work. Please contact HR.")
        self.assertFalse(Shift.objects.exists())

    def test_expired_revoked_and_failed_statuses_are_all_refused_alike(self):
        expired = self.hire("expired@example.com", self.store_a)
        self.clear(expired, "Graduate", expiry=day(-1))
        revoked = self.hire("revoked@example.com", self.store_a)
        visa_services.revoke_visa(visa=self.clear(revoked), by=self.hr)
        for person in (expired, revoked):
            with self.subTest(person.user.email):
                response = self.add(self.manager_a, self.week_a, person)
                self.assertEqual(self.codes(response), ["not_cleared"])
                body = json.dumps(response.data).lower()
                for leak in ("visa", "expire", "revoke", "graduate", "student", "check"):
                    self.assertNotIn(leak, body)

    def test_status_that_expires_mid_week_blocks_only_the_days_after(self):
        person = self.hire("grad@example.com", self.store_a)
        self.clear(person, "Graduate", expiry=day(2))
        self.assertEqual(self.add(self.manager_a, self.week_a, person, n=2).status_code, 201)
        self.assertEqual(self.codes(self.add(self.manager_a, self.week_a, person, n=3)), ["not_cleared"])

    def test_student_weekly_cap_is_enforced_to_the_hour(self):
        student = self.make_student()
        for n, end in ((0, "17:00"), (1, "17:00"), (2, "13:00")):  # 8 + 8 + 4 = 20
            self.assertEqual(self.add(self.manager_a, self.week_a, student, n=n, end=end).status_code, 201)
        over = self.add(self.manager_a, self.week_a, student, n=3, start="09:00", end="10:00")
        self.assertEqual(over.status_code, 400)
        self.assertEqual(self.codes(over), ["hours_limit"])
        self.assertEqual(over.data["blocks"][0]["message"], "Over the weekly hours limit of 20.")

    def test_hours_across_both_stores_count_towards_the_cap(self):
        student = self.make_student()
        self.cover(student, self.store_b)
        self.assertEqual(self.add(self.manager_a, self.week_a, student, n=0).status_code, 201)
        self.assertEqual(self.add(self.manager_a, self.week_a, student, n=1).status_code, 201)
        over = self.add(self.manager_b, self.week_b, student, n=2)  # 8 more at the other shop
        self.assertEqual(self.codes(over), ["hours_limit"])
        self.assertEqual(self.add(self.manager_b, self.week_b, student, n=2, end="13:00").status_code, 201)

    def test_cancelling_a_shift_frees_the_hours(self):
        student = self.make_student()
        first = self.add(self.manager_a, self.week_a, student, n=0)
        self.add(self.manager_a, self.week_a, student, n=1)
        self.assertEqual(self.codes(self.add(self.manager_a, self.week_a, student, n=2)), ["hours_limit"])
        self.client.post(reverse("shift-cancel", args=[first.data["id"]]))
        self.assertEqual(self.add(self.manager_a, self.week_a, student, n=2).status_code, 201)

    def test_the_cap_counts_one_monday_to_sunday_week_at_a_time(self):
        student = self.make_student()
        earlier = services.create_week(store=self.store_a, week_start=NEXT_MON - timedelta(days=7), by=self.hr)[0]
        sunday = self.add(self.manager_a, earlier, student, n=-1)  # the Sunday before: a different week
        self.assertEqual(sunday.status_code, 201, sunday.data)
        for n, end in ((0, "17:00"), (1, "17:00"), (2, "13:00")):
            self.assertEqual(self.add(self.manager_a, self.week_a, student, n=n, end=end).status_code, 201)

    def test_vacation_lifts_the_cap(self):
        student = self.make_student()
        visa = EmployeeVisa.objects.get(employee=student, is_current=True)
        VacationPeriod.objects.create(visa=visa, start_date=day(0), end_date=day(6))
        for n in range(3):
            self.assertEqual(self.add(self.manager_a, self.week_a, student, n=n).status_code, 201)  # 24 hours

    def test_a_person_from_another_store_is_not_visible_to_the_manager(self):
        response = self.add(self.manager_a, self.week_a, self.emp_b)
        self.assertEqual(response.status_code, 400)
        self.assertIn("employee", response.data)

    def test_hr_cannot_roster_someone_at_a_store_they_are_not_assigned_to(self):
        response = self.add(self.hr, self.week_a, self.emp_b)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.codes(response), ["not_assigned_to_store"])

    def test_approved_cover_allows_work_at_the_other_store_for_those_dates_only(self):
        self.cover(self.emp_b, self.store_a, first=0, last=2)
        self.assertEqual(self.add(self.manager_a, self.week_a, self.emp_b, n=0).status_code, 201)
        self.assertEqual(self.add(self.manager_a, self.week_a, self.emp_b, n=2).status_code, 201)
        outside = self.add(self.manager_a, self.week_a, self.emp_b, n=3)
        self.assertEqual(self.codes(outside), ["not_assigned_to_store"])

    def test_pending_cover_does_not_count(self):
        self.login(self.manager_a)
        request = self.client.post(reverse("cover-request-list"), {
            "employee": self.emp_a.pk, "store": self.store_b.pk,
            "valid_from": day(0).isoformat(), "valid_to": day(2).isoformat(),
        })
        self.assertEqual(request.status_code, 201)
        self.assertEqual(request.data["status"], "pending")
        response = self.add(self.hr, self.week_b, self.emp_a)
        self.assertEqual(self.codes(response), ["not_assigned_to_store"])

    def test_overlapping_shifts_are_refused(self):
        self.add(self.manager_a, self.week_a, self.emp_a, start="09:00", end="13:00")
        clash = self.add(self.manager_a, self.week_a, self.emp_a, start="12:00", end="16:00")
        self.assertEqual(self.codes(clash), ["double_booked"])
        self.assertEqual(self.add(self.manager_a, self.week_a, self.emp_a, start="13:00", end="17:00").status_code, 201)

    def test_overnight_shift_blocks_the_start_of_the_next_day(self):
        self.add(self.manager_a, self.week_a, self.emp_a, n=0, start="22:00", end="06:00")
        clash = self.add(self.manager_a, self.week_a, self.emp_a, n=1, start="05:00", end="09:00")
        self.assertEqual(self.codes(clash), ["double_booked"])

    def test_double_booking_is_caught_across_stores(self):
        self.cover(self.emp_a, self.store_b)
        self.add(self.manager_a, self.week_a, self.emp_a, start="09:00", end="17:00")
        clash = self.add(self.manager_b, self.week_b, self.emp_a, start="12:00", end="18:00")
        self.assertEqual(self.codes(clash), ["double_booked"])
        self.assertNotIn("Shop", json.dumps(clash.data))  # no detail about the other store's rota

    def test_cancelled_shifts_do_not_block_anything(self):
        first = self.add(self.manager_a, self.week_a, self.emp_a)
        self.client.post(reverse("shift-cancel", args=[first.data["id"]]))
        self.assertEqual(self.add(self.manager_a, self.week_a, self.emp_a).status_code, 201)

    def test_people_must_be_employed_on_the_date(self):
        leaving = self.hire("leaving@example.com", self.store_a)
        self.clear(leaving)
        leaving.end_date = day(1)
        leaving.save()
        self.assertEqual(self.add(self.manager_a, self.week_a, leaving, n=1).status_code, 201)
        self.assertEqual(self.codes(self.add(self.manager_a, self.week_a, leaving, n=2)), ["not_employed"])

        starting = self.hire("starting@example.com", self.store_a, start=day(3).toordinal() - TODAY.toordinal())
        self.clear(starting)
        self.assertEqual(self.codes(self.add(self.manager_a, self.week_a, starting, n=1)), ["not_employed"])
        self.assertEqual(self.add(self.manager_a, self.week_a, starting, n=3).status_code, 201)

    def test_deactivated_accounts_cannot_be_rostered(self):
        self.emp_a.user.is_active = False
        self.emp_a.user.save()
        self.assertEqual(self.codes(self.add(self.manager_a, self.week_a, self.emp_a)), ["not_employed"])

    def test_every_problem_is_reported_together(self):
        newcomer = self.hire("new@example.com", self.store_a)
        response = self.add(self.hr, self.week_b, newcomer)  # wrong store AND not cleared
        self.assertEqual(sorted(self.codes(response)), ["not_assigned_to_store", "not_cleared"])


class ShiftChangeTests(RotaTestBase):
    def shift(self, employee=None, **kwargs):
        response = self.add(self.manager_a, self.week_a, employee or self.emp_a, **kwargs)
        self.assertEqual(response.status_code, 201, response.data)
        return response.data["id"]

    def patch(self, shift_id, body, user=None):
        self.login(user or self.manager_a)
        return self.client.patch(reverse("shift-detail", args=[shift_id]), body)

    def test_reassigning_to_someone_not_cleared_is_refused_and_nothing_changes(self):
        shift_id = self.shift()
        newcomer = self.hire("new@example.com", self.store_a)
        response = self.patch(shift_id, {"employee": newcomer.pk})
        self.assertEqual(self.codes(response), ["not_cleared"])
        self.assertEqual(Shift.objects.get(pk=shift_id).employee, self.emp_a)

    def test_a_shift_is_not_counted_twice_when_it_is_changed(self):
        student = self.make_student()
        self.shift(student, n=0)
        second = self.shift(student, n=1)  # 16 hours so far
        response = self.patch(second, {"end_time": "19:00"})  # 10 hours: 18 in total
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["hours"], "10.00")
        over = self.patch(second, {"end_time": "22:00"})  # 13 hours: 21 in total
        self.assertEqual(self.codes(over), ["hours_limit"])

    def test_notes_and_role_can_change_even_if_the_person_is_no_longer_cleared(self):
        shift_id = self.shift()
        EmployeeVisa.objects.filter(employee=self.emp_a).update(revoked_on=TODAY)
        response = self.patch(shift_id, {"notes": "Bring ID", "role": "Stock"})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data["notes"], response.data["role"]), ("Bring ID", "Stock"))
        moved = self.patch(shift_id, {"end_time": "18:00"})  # but changing the time re-checks
        self.assertEqual(self.codes(moved), ["not_cleared"])

    def test_a_shift_can_be_handed_back_as_an_open_shift(self):
        shift_id = self.shift()
        response = self.patch(shift_id, {"employee": None})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data["employee"])

    def test_an_open_shift_can_be_assigned(self):
        shift_id = self.add(self.manager_a, self.week_a).data["id"]
        response = self.patch(shift_id, {"employee": self.emp_a.pk})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["employee"]["id"], self.emp_a.pk)

    def test_a_shift_cannot_move_outside_its_week(self):
        shift_id = self.shift()
        self.assertEqual(self.codes(self.patch(shift_id, {"date": day(8).isoformat()})), ["date_outside_week"])

    def test_cancelling(self):
        shift_id = self.shift()
        self.login(self.manager_a)
        url = reverse("shift-cancel", args=[shift_id])
        self.assertEqual(self.client.post(url).data["status"], "cancelled")
        self.assertEqual(self.codes(self.client.post(url)), ["already_cancelled"])
        self.assertEqual(self.codes(self.patch(shift_id, {"notes": "x"})), ["shift_cancelled"])

    def test_shifts_that_have_started_cannot_be_changed_or_cancelled(self):
        shift_id = self.shift(n=0)
        with pretend_today(day(1)):
            self.assertEqual(self.codes(self.patch(shift_id, {"notes": "x"})), ["shift_in_past"])
            self.login(self.manager_a)
            cancel = self.client.post(reverse("shift-cancel", args=[shift_id]))
            self.assertEqual(self.codes(cancel), ["shift_in_past"])
        self.assertEqual(Shift.objects.get(pk=shift_id).status, ShiftStatus.SCHEDULED)

    def test_shifts_cannot_be_deleted(self):
        shift_id = self.shift()
        self.login(self.owner)
        self.assertEqual(self.client.delete(reverse("shift-detail", args=[shift_id])).status_code, 405)


class WarningTests(RotaTestBase):
    def test_short_rest_between_shifts_warns_but_saves(self):
        self.add(self.manager_a, self.week_a, self.emp_a, n=0, start="14:00", end="23:00")
        response = self.add(self.manager_a, self.week_a, self.emp_a, n=1, start="06:00", end="10:00")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(self.warning_codes(response), ["short_rest"])

    def test_exactly_eleven_hours_rest_is_fine(self):
        self.add(self.manager_a, self.week_a, self.emp_a, n=0, start="14:00", end="23:00")
        response = self.add(self.manager_a, self.week_a, self.emp_a, n=1, start="10:00", end="14:00")
        self.assertEqual(response.data["warnings"], [])

    def test_a_week_over_48_hours_warns(self):
        for n in range(4):
            response = self.add(self.manager_a, self.week_a, self.emp_a, n=n, start="08:00", end="18:00")
            self.assertEqual(response.data["warnings"], [], n)  # 10, 20, 30, 40
        fifth = self.add(self.manager_a, self.week_a, self.emp_a, n=4, start="08:00", end="18:00")
        self.assertEqual(fifth.status_code, 201)
        self.assertEqual(self.warning_codes(fifth), ["long_week"])

    def test_opening_hours_warnings(self):
        StoreHours.objects.create(store=self.store_a, weekday=0, opens="07:00", closes="23:00")
        StoreHours.objects.create(store=self.store_a, weekday=6, is_closed=True)
        StoreHours.objects.create(store=self.store_a, weekday=4, opens="07:00", closes="01:00")
        cases = [
            (0, "07:00", "15:00", []),
            (0, "06:00", "14:00", ["outside_opening_hours"]),
            (0, "16:00", "23:30", ["outside_opening_hours"]),
            (6, "09:00", "13:00", ["store_closed"]),
            (2, "03:00", "09:00", []),                      # Wednesday: no hours set up, nothing to compare
            (4, "18:00", "00:30", []),                      # open past midnight
            (4, "18:00", "02:00", ["outside_opening_hours"]),
        ]
        for n, start, end, expected in cases:
            with self.subTest(day=n, start=start, end=end):
                response = self.add(self.manager_a, self.week_a, n=n, start=start, end=end)
                self.assertEqual(response.status_code, 201, response.data)
                self.assertEqual(self.warning_codes(response), expected)

    def test_warnings_never_reveal_immigration_details(self):
        student = self.hire("grad@example.com", self.store_a)
        self.clear(student, "Graduate", expiry=day(10))  # expiring soon
        response = self.add(self.manager_a, self.week_a, student)
        self.assertEqual(response.status_code, 201)
        body = json.dumps(response.data).lower()
        for leak in ("expire", "graduate", "visa", "immigration", "right to work"):
            self.assertNotIn(leak, body)


class AccessTests(RotaTestBase):
    def setUp(self):
        super().setUp()
        self.shift_a = Shift.objects.create(
            rota_week=self.week_a, employee=self.emp_a, date=day(0), start_time="09:00", end_time="17:00"
        )
        self.shift_b = Shift.objects.create(
            rota_week=self.week_b, employee=self.emp_b, date=day(0), start_time="09:00", end_time="17:00"
        )

    def test_manager_sees_only_their_own_stores_rota(self):
        self.login(self.manager_a)
        weeks = self.client.get(reverse("rota-week-list"), {"week_start": NEXT_MON.isoformat()})
        self.assertEqual([row["store"]["name"] for row in weeks.data], ["Shop A"])
        self.assertEqual(self.client.get(reverse("rota-week-detail", args=[self.week_a.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("rota-week-detail", args=[self.week_b.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("shift-detail", args=[self.shift_b.pk])).status_code, 404)

    def test_manager_cannot_change_another_stores_rota(self):
        self.login(self.manager_a)
        self.assertEqual(self.add(self.manager_a, self.week_b, self.emp_b).status_code, 404)
        self.assertEqual(self.client.patch(reverse("shift-detail", args=[self.shift_b.pk]), {"notes": "x"}).status_code, 404)
        self.assertEqual(self.client.post(reverse("shift-cancel", args=[self.shift_b.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("rota-week-publish", args=[self.week_b.pk])).status_code, 404)
        made = self.client.post(reverse("rota-week-list"), {"store": self.store_b.pk, "week_start": day(7).isoformat()})
        self.assertEqual(made.status_code, 404)
        self.assertFalse(RotaWeek.objects.filter(store=self.store_b, week_start=day(7)).exists())

    def test_owner_and_hr_see_every_shop_on_one_screen(self):
        for user in (self.owner, self.hr):
            self.login(user)
            response = self.client.get(
                reverse("rota-week-list"), {"week_start": NEXT_MON.isoformat(), "with_shifts": "1"}
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual({row["store"]["name"] for row in response.data}, {"Shop A", "Shop B"})
            self.assertEqual(sum(len(row["shifts"]) for row in response.data), 2)

    def test_owner_and_hr_can_edit_any_stores_rota(self):
        for user in (self.owner, self.hr):
            self.assertEqual(self.add(user, self.week_b, n=1).status_code, 201)
            self.login(user)
            self.assertEqual(
                self.client.patch(reverse("shift-detail", args=[self.shift_b.pk]), {"notes": "ok"}).status_code, 200
            )

    def test_list_can_be_filtered_by_store(self):
        self.login(self.owner)
        response = self.client.get(reverse("rota-week-list"), {"store": self.store_b.pk})
        self.assertEqual([row["store"]["name"] for row in response.data], ["Shop B"])

    def test_week_list_shows_how_many_shifts_are_scheduled(self):
        Shift.objects.create(
            rota_week=self.week_a, date=day(1), start_time="09:00", end_time="12:00", status=ShiftStatus.CANCELLED
        )
        self.login(self.manager_a)
        row = self.client.get(reverse("rota-week-list")).data[0]
        self.assertEqual(row["scheduled_shifts"], 1)
        self.assertEqual(row["week_end"], day(6))

    def test_employees_and_anonymous_users_cannot_use_the_management_endpoints(self):
        endpoints = [
            ("get", reverse("rota-week-list")),
            ("post", reverse("rota-week-list")),
            ("get", reverse("rota-week-detail", args=[self.week_a.pk])),
            ("post", reverse("rota-week-publish", args=[self.week_a.pk])),
            ("post", reverse("rota-week-shifts", args=[self.week_a.pk])),
            ("get", reverse("shift-detail", args=[self.shift_a.pk])),
            ("patch", reverse("shift-detail", args=[self.shift_a.pk])),
            ("post", reverse("shift-cancel", args=[self.shift_a.pk])),
            ("get", reverse("shift-template-list")),
            ("post", reverse("shift-template-list")),
        ]
        self.login(self.emp_a.user)
        for method, url in endpoints:
            with self.subTest(user="employee", method=method, url=url):
                self.assertEqual(getattr(self.client, method)(url, {}).status_code, 403)
        self.client.force_authenticate(None)
        for method, url in endpoints:
            with self.subTest(user="anonymous", method=method, url=url):
                self.assertEqual(getattr(self.client, method)(url, {}).status_code, 401)


class PublishTests(RotaTestBase):
    def test_publishing_makes_the_rota_visible_to_staff(self):
        self.add(self.manager_a, self.week_a, self.emp_a)
        self.login(self.emp_a.user)
        before = self.client.get(reverse("my-rota"))
        self.assertEqual(before.data, [])  # still a draft

        self.login(self.manager_a)
        published = self.client.post(reverse("rota-week-publish", args=[self.week_a.pk]))
        self.assertEqual(published.status_code, 200, published.data)
        self.assertEqual(published.data["status"], "published")
        self.week_a.refresh_from_db()
        self.assertEqual(self.week_a.published_by, self.manager_a)
        self.assertIsNotNone(self.week_a.published_at)

        self.login(self.emp_a.user)
        self.assertEqual(len(self.client.get(reverse("my-rota")).data), 1)

    def test_publishing_twice_is_refused(self):
        self.login(self.manager_a)
        url = reverse("rota-week-publish", args=[self.week_a.pk])
        self.client.post(url)
        self.assertEqual(self.codes(self.client.post(url)), ["already_published"])

    def test_publish_rechecks_everyone_and_refuses_if_someone_is_no_longer_cleared(self):
        shift_id = self.add(self.manager_a, self.week_a, self.emp_a).data["id"]
        EmployeeVisa.objects.filter(employee=self.emp_a).update(revoked_on=TODAY)  # after drafting
        self.login(self.manager_a)
        response = self.client.post(reverse("rota-week-publish", args=[self.week_a.pk]))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.codes(response), ["not_cleared"])
        self.assertEqual(response.data["blocks"][0]["shift"], str(shift_id))
        self.week_a.refresh_from_db()
        self.assertEqual(self.week_a.status, WeekStatus.DRAFT)

    def test_publish_rechecks_the_weekly_cap_across_stores(self):
        student = self.make_student()
        self.cover(student, self.store_b)
        self.add(self.manager_a, self.week_a, student, n=0)
        self.add(self.manager_a, self.week_a, student, n=1)
        shift_b = Shift.objects.create(  # slipped in around the checks (e.g. via the admin)
            rota_week=self.week_b, employee=student, date=day(2), start_time="09:00", end_time="17:00"
        )
        self.login(self.manager_b)
        response = self.client.post(reverse("rota-week-publish", args=[self.week_b.pk]))
        self.assertEqual(self.codes(response), ["hours_limit"])
        self.assertEqual(response.data["blocks"][0]["shift"], str(shift_b.pk))

    def test_shifts_already_worked_do_not_stop_publishing(self):
        self.add(self.manager_a, self.week_a, self.emp_a, n=0)
        later = self.add(self.manager_a, self.week_a, self.emp_a, n=4).data["id"]
        EmployeeVisa.objects.filter(employee=self.emp_a).update(revoked_on=TODAY)
        with pretend_today(day(3)):
            self.login(self.manager_a)
            url = reverse("rota-week-publish", args=[self.week_a.pk])
            self.assertEqual(self.client.post(url).status_code, 400)  # the shift still to come is a problem
            self.client.post(reverse("shift-cancel", args=[later]))
            self.assertEqual(self.client.post(url).status_code, 200)  # the one already worked is ignored

    def test_changes_after_publishing_are_allowed_and_staff_see_them(self):
        shift_id = self.add(self.manager_a, self.week_a, self.emp_a).data["id"]
        self.login(self.manager_a)
        self.client.post(reverse("rota-week-publish", args=[self.week_a.pk]))
        changed = self.client.patch(reverse("shift-detail", args=[shift_id]), {"start_time": "10:00"})
        self.assertEqual(changed.status_code, 200, changed.data)
        self.login(self.emp_a.user)
        self.assertEqual(self.client.get(reverse("my-rota")).data[0]["start_time"], "10:00:00")

    def test_an_empty_week_can_be_published(self):
        self.login(self.manager_a)
        self.assertEqual(self.client.post(reverse("rota-week-publish", args=[self.week_a.pk])).status_code, 200)

    def codes(self, response):
        return [block["code"] for block in response.data["blocks"]]


class MyRotaTests(RotaTestBase):
    def publish(self, week):
        week.status = WeekStatus.PUBLISHED
        week.save()

    def mine(self, **params):
        self.login(self.emp_a.user)
        response = self.client.get(reverse("my-rota"), params)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_only_my_own_published_shifts(self):
        self.add(self.manager_a, self.week_a, self.emp_a, n=0, notes="Private note")
        self.add(self.manager_a, self.week_a, n=1)                    # open shift
        other = self.hire("other@example.com", self.store_a)
        self.clear(other)
        self.add(self.manager_a, self.week_a, other, n=2)             # someone else's
        self.publish(self.week_a)
        later = services.create_week(store=self.store_a, week_start=day(7), by=self.hr)[0]
        self.add(self.manager_a, later, self.emp_a, n=7)              # in a draft week
        rows = self.mine(to=day(13).isoformat())
        self.assertEqual([row["date"] for row in rows], [str(day(0))])
        self.assertEqual(rows[0]["store"]["name"], "Shop A")
        self.assertEqual(rows[0]["hours"], "8.00")

    def test_private_manager_notes_and_other_staff_are_never_shown(self):
        other = self.hire("other@example.com", self.store_a)
        self.clear(other)
        self.add(self.manager_a, self.week_a, self.emp_a, n=0, notes="Late twice this month")
        self.add(self.manager_a, self.week_a, other, n=1, notes="Disciplinary meeting")
        self.publish(self.week_a)
        body = json.dumps(self.mine())
        self.assertNotIn("notes", body)
        self.assertNotIn("Late twice", body)
        self.assertNotIn("Disciplinary", body)
        self.assertNotIn("employee", body)

    def test_shifts_at_another_store_on_approved_cover_appear(self):
        self.cover(self.emp_a, self.store_b)
        self.add(self.manager_a, self.week_a, self.emp_a, n=0)
        self.add(self.manager_b, self.week_b, self.emp_a, n=1)
        self.publish(self.week_a)
        self.publish(self.week_b)
        self.assertEqual([row["store"]["name"] for row in self.mine()], ["Shop A", "Shop B"])

    def test_cancelled_shifts_still_show_so_staff_know(self):
        shift_id = self.add(self.manager_a, self.week_a, self.emp_a).data["id"]
        self.publish(self.week_a)
        self.client.force_authenticate(self.manager_a)
        self.client.post(reverse("shift-cancel", args=[shift_id]))
        self.assertEqual(self.mine()[0]["status"], "cancelled")

    def test_date_range(self):
        self.add(self.manager_a, self.week_a, self.emp_a, n=0)
        self.add(self.manager_a, self.week_a, self.emp_a, n=3)
        self.publish(self.week_a)
        self.assertEqual(len(self.mine(**{"from": day(2).isoformat(), "to": day(5).isoformat()})), 1)
        self.assertEqual(len(self.mine(**{"from": day(0).isoformat(), "to": day(0).isoformat()})), 1)
        self.assertEqual(len(self.mine()), 2)  # default: today and the next two weeks

    def test_bad_ranges_are_refused(self):
        self.login(self.emp_a.user)
        for params in ({"from": "nope"}, {"from": day(5).isoformat(), "to": day(1).isoformat()},
                       {"from": day(0).isoformat(), "to": day(200).isoformat()}):
            with self.subTest(params):
                self.assertEqual(self.client.get(reverse("my-rota"), params).status_code, 400)

    def test_people_without_a_profile_get_404_and_anonymous_get_401(self):
        self.login(self.owner)
        self.assertEqual(self.client.get(reverse("my-rota")).status_code, 404)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(reverse("my-rota")).status_code, 401)


class WeekCreationTests(RotaTestBase):
    def create(self, user, store, week_start, **extra):
        self.login(user)
        return self.client.post(
            reverse("rota-week-list"), {"store": store.pk, "week_start": week_start.isoformat(), **extra}
        )

    def codes(self, response):
        return [block["code"] for block in response.data["blocks"]]

    def test_manager_starts_a_new_week(self):
        response = self.create(self.manager_a, self.store_a, day(7))
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], "draft")
        self.assertEqual(response.data["shifts"], [])
        self.assertEqual(response.data["copy_summary"]["copied"], 0)

    def test_week_rules(self):
        cases = {
            "week_not_monday": (self.store_a, day(8)),
            "week_exists": (self.store_a, day(0)),
        }
        for code, (store, start) in cases.items():
            with self.subTest(code):
                self.assertEqual(self.codes(self.create(self.manager_a, store, start)), [code])
        with self.subTest("week_in_past"):
            self.assertEqual(self.codes(self.create(self.hr, self.store_a, day(-14))), ["week_in_past"])
        with self.subTest("store_inactive"):
            Store.objects.filter(pk=self.store_b.pk).update(is_active=False)
            self.assertEqual(self.codes(self.create(self.hr, self.store_b, day(7))), ["store_inactive"])

    def test_the_week_in_progress_can_still_be_created(self):
        this_monday = TODAY - timedelta(days=TODAY.weekday())
        self.assertEqual(self.create(self.manager_a, self.store_a, this_monday).status_code, 201)

    def test_copy_last_week_moves_every_shift_forward_seven_days(self):
        other = self.hire("other@example.com", self.store_a)
        self.clear(other)
        self.add(self.manager_a, self.week_a, self.emp_a, n=0, role="Cashier")
        self.add(self.manager_a, self.week_a, other, n=2, start="14:00", end="22:00", break_minutes=30)
        self.add(self.manager_a, self.week_a, n=5)  # open shift copies too
        cancelled = self.add(self.manager_a, self.week_a, self.emp_a, n=4).data["id"]
        self.client.post(reverse("shift-cancel", args=[cancelled]))

        response = self.create(self.manager_a, self.store_a, day(7), copy_from_previous=True)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["copy_summary"], {"copied": 3, "made_open": [], "skipped_past": 0})
        rows = response.data["shifts"]
        self.assertEqual([row["date"] for row in rows], [str(day(7)), str(day(9)), str(day(12))])
        self.assertEqual(rows[0]["role"], "Cashier")
        self.assertEqual(rows[0]["employee"]["id"], self.emp_a.pk)
        self.assertEqual((rows[1]["start_time"], rows[1]["hours"]), ("14:00:00", "7.50"))
        self.assertIsNone(rows[2]["employee"])
        self.assertEqual(self.week_a.shifts.count(), 4)  # the source week is untouched

    def test_people_who_can_no_longer_work_become_open_shifts(self):
        leaver = self.hire("grad@example.com", self.store_a)
        self.clear(leaver, "Graduate", expiry=day(3))
        self.add(self.manager_a, self.week_a, leaver, n=0)
        self.add(self.manager_a, self.week_a, self.emp_a, n=1)
        response = self.create(self.manager_a, self.store_a, day(7), copy_from_previous=True)
        self.assertEqual(response.status_code, 201, response.data)
        summary = response.data["copy_summary"]
        self.assertEqual(summary["copied"], 2)
        self.assertEqual(len(summary["made_open"]), 1)
        self.assertEqual(summary["made_open"][0]["message"], "Not cleared to work. Please contact HR.")
        employees = [row["employee"] and row["employee"]["id"] for row in response.data["shifts"]]
        self.assertEqual(employees, [None, self.emp_a.pk])

    def test_copying_respects_the_weekly_cap(self):
        student = self.make_student()
        for n in range(3):
            self.add(self.manager_a, self.week_a, student, n=n, end="13:00")  # 3 x 4 = 12 hours
        VisaType.objects.filter(name="Student").update(max_weekly_hours=10)   # the rule tightens
        response = self.create(self.manager_a, self.store_a, day(7), copy_from_previous=True)
        made_open = response.data["copy_summary"]["made_open"]
        self.assertEqual(len(made_open), 1)  # the third 4-hour shift would take the week over 10
        self.assertEqual(made_open[0]["message"], "Over the weekly hours limit of 10.")

    def test_copying_into_a_week_in_progress_skips_days_that_have_passed(self):
        for n in range(7):
            self.add(self.manager_a, self.week_a, self.emp_a, n=n, start="09:00", end="12:00")
        with pretend_today(day(10)):  # Thursday of the following week
            response = self.create(self.manager_a, self.store_a, day(7), copy_from_previous=True)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["copy_summary"]["skipped_past"], 3)  # Mon, Tue, Wed
        self.assertEqual(response.data["copy_summary"]["copied"], 4)
        self.assertEqual(response.data["shifts"][0]["date"], str(day(10)))

    def test_copy_needs_a_previous_week_and_creates_nothing_otherwise(self):
        response = self.create(self.manager_a, self.store_a, day(14), copy_from_previous=True)
        self.assertEqual(self.codes(response), ["no_previous_week"])
        self.assertFalse(RotaWeek.objects.filter(store=self.store_a, week_start=day(14)).exists())

    def test_copy_ignores_other_stores(self):
        self.add(self.manager_b, self.week_b, self.emp_b)
        response = self.create(self.manager_a, self.store_a, day(7), copy_from_previous=True)
        self.assertEqual(response.data["copy_summary"]["copied"], 0)


class ShiftTemplateTests(RotaTestBase):
    def create(self, user, store, **extra):
        self.login(user)
        body = {"store": store.pk, "name": "Opening", "start_time": "07:00", "end_time": "15:00"}
        body.update(extra)
        return self.client.post(reverse("shift-template-list"), body)

    def test_manager_manages_templates_for_their_own_store(self):
        response = self.create(self.manager_a, self.store_a, break_minutes=30)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["store"], self.store_a.pk)
        url = reverse("shift-template-detail", args=[response.data["id"]])
        changed = self.client.patch(url, {"end_time": "16:00", "is_active": False, "store": self.store_b.pk})
        self.assertEqual(changed.status_code, 200)
        template = ShiftTemplate.objects.get(pk=response.data["id"])
        self.assertEqual((str(template.end_time), template.is_active, template.store), ("16:00:00", False, self.store_a))

    def test_no_duplicate_names_and_no_zero_length_templates(self):
        self.create(self.manager_a, self.store_a)
        self.assertEqual(self.create(self.manager_a, self.store_a).status_code, 400)
        self.assertEqual(self.create(self.manager_a, self.store_a, name="Odd", end_time="07:00").status_code, 400)
        self.assertEqual(self.create(self.manager_b, self.store_b).status_code, 201)  # same name, other store

    def test_managers_cannot_touch_other_stores_templates(self):
        theirs = self.create(self.manager_b, self.store_b)
        self.assertEqual(self.create(self.manager_a, self.store_b, name="Mine").status_code, 404)
        self.login(self.manager_a)
        self.assertEqual(self.client.get(reverse("shift-template-list")).data, [])
        url = reverse("shift-template-detail", args=[theirs.data["id"]])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.patch(url, {"name": "Hijack"}).status_code, 404)

    def test_owner_sees_all_and_templates_cannot_be_deleted(self):
        self.create(self.manager_a, self.store_a)
        made = self.create(self.manager_b, self.store_b)
        self.login(self.owner)
        self.assertEqual(len(self.client.get(reverse("shift-template-list")).data), 2)
        self.assertEqual(len(self.client.get(reverse("shift-template-list"), {"store": self.store_b.pk}).data), 1)
        self.assertEqual(self.client.delete(reverse("shift-template-detail", args=[made.data["id"]])).status_code, 405)
