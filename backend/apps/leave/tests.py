from datetime import date, time, timedelta
from decimal import Decimal
from unittest import mock

from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from apps.employees import services as employee_services
from apps.notifications.models import Notification
from apps.notifications.test_alerts import inbox, queued, send_everything
from apps.rota import services as rota_services
from apps.rota.tests import RotaTestBase, day

from . import services
from .models import LeaveAllowance, LeaveRequest, LeaveStatus
from .rules import days_in_range, default_allowance, leave_year

TODAY = date.today()


class RulesTests(SimpleTestCase):
    def test_the_leave_year_follows_the_setting(self):
        self.assertEqual(leave_year(date(2026, 10, 25), 1), (date(2026, 1, 1), date(2026, 12, 31)))
        self.assertEqual(leave_year(date(2027, 3, 31), 4), (date(2026, 4, 1), date(2027, 3, 31)))
        self.assertEqual(leave_year(date(2027, 4, 1), 4), (date(2027, 4, 1), date(2028, 3, 31)))

    def test_days_asked_for(self):
        self.assertEqual(days_in_range(date(2026, 11, 2), date(2026, 11, 6)), Decimal("5"))
        self.assertEqual(days_in_range(date(2026, 11, 2), date(2026, 11, 2)), Decimal("1"))
        self.assertEqual(days_in_range(date(2026, 11, 2), date(2026, 11, 2), "am"), Decimal("0.5"))

    def test_the_default_allowance_is_shared_out_by_days_employed_and_rounded_up_to_a_half_day(self):
        first, last = date(2026, 1, 1), date(2026, 12, 31)

        def person(start, end=None):
            return type("P", (), {"start_date": start, "end_date": end})()

        self.assertEqual(default_allowance(person(date(2020, 1, 1)), first, last, 28), Decimal("28"))
        self.assertEqual(default_allowance(person(date(2026, 7, 1)), first, last, 28), Decimal("14.5"))  # 184/365 days
        self.assertEqual(default_allowance(person(date(2020, 1, 1), date(2026, 6, 30)), first, last, 28), Decimal("14"))  # 181/365 days
        self.assertEqual(default_allowance(person(date(2027, 2, 1)), first, last, 28), Decimal("0"))
        self.assertEqual(default_allowance(person(date(2020, 1, 1), date(2025, 6, 1)), first, last, 28), Decimal("0"))


class LeaveBase(RotaTestBase):
    def setUp(self):
        super().setUp()
        # A manager is a worker too, and their own leave goes to the Owner/HR.
        self.mgr_profile = employee_services.create_employee(
            user=self.manager_a, home_store=self.store_a, start_date=TODAY - timedelta(days=400), created_by=self.hr,
        )
        self.set_allowance(self.emp_a, 10)

    def set_allowance(self, employee, days):
        services.set_allowance(employee=employee, on=day(0), days=Decimal(str(days)), by=self.hr)

    def ask(self, user, kind="annual", n=0, to=None, **extra):
        self.login(user)
        body = {"kind": kind, "start_date": day(n).isoformat(), "end_date": day(n if to is None else to).isoformat()}
        body.update(extra)
        return self.client.post(reverse("my-leave"), body)

    def decide(self, user, action, pk, **body):
        self.login(user)
        return self.client.post(reverse(f"leave-{action}", args=[pk]), body)

    def approved(self, employee=None, n=0, to=None, **extra):
        employee = employee or self.emp_a
        leave = self.ask(employee.user, n=n, to=to, **extra).data["id"]
        self.assertEqual(self.decide(self.manager_a, "approve", leave).status_code, 200)
        return LeaveRequest.objects.get(pk=leave)

    def errors(self, response):
        return str(response.data)

    def shift(self, employee, n, week=None):
        return rota_services.create_shift(
            rota_week=week or self.week_a, by=self.hr, employee=employee, date=day(n),
            start_time=time(9), end_time=time(17),
        )


class RequestingTests(LeaveBase):
    def test_an_employee_asks_and_only_their_stores_manager_is_told(self):
        response = self.ask(self.emp_a.user, n=0, to=2, note="Family wedding")
        self.assertEqual(response.status_code, 201)
        self.assertEqual((response.data["status"], response.data["days_requested"]), ("pending", "3.0"))
        self.assertEqual(queued("leave_requested"), ["mgr-a@example.com"])  # not Shop B's manager, not HR

    def test_a_managers_own_leave_goes_to_owner_and_hr_not_to_other_managers(self):
        self.assertEqual(self.ask(self.manager_a, n=0).status_code, 201)
        self.assertEqual(queued("leave_requested"), ["hr@example.com", "owner@example.com"])

    def test_the_dates_have_to_make_sense(self):
        for body, field in [
            ({"n": 3, "to": 1}, "end_date"),
            ({"n": 0, "to": 3, "half_day": "am"}, "half_day"),
        ]:
            response = self.ask(self.emp_a.user, **body)
            self.assertEqual(response.status_code, 400, body)
            self.assertIn(field, response.data)
        self.assertEqual(LeaveRequest.objects.count(), 0)

    def test_one_request_is_at_most_sixty_days(self):
        with override_settings(LEAVE_YEAR_START_MONTH=day(0).month):  # keep both ends in one leave year
            too_long = self.ask(self.emp_a.user, kind="unpaid", n=0, to=60)  # 61 days
            self.assertEqual(too_long.status_code, 400)
            self.assertIn("end_date", too_long.data)
            self.assertEqual(self.ask(self.emp_a.user, kind="unpaid", n=0, to=59).status_code, 201)  # exactly 60

    def test_annual_and_unpaid_leave_must_start_in_the_future_but_other_leave_may_start_today(self):
        for kind in ("annual", "unpaid"):
            self.login(self.emp_a.user)
            response = self.client.post(reverse("my-leave"), {"kind": kind, "start_date": TODAY.isoformat()})
            self.assertEqual(response.status_code, 400, kind)
            self.assertIn("start_date", response.data)
        self.login(self.emp_a.user)
        yesterday = (TODAY - timedelta(days=1)).isoformat()
        self.assertEqual(self.client.post(reverse("my-leave"), {"kind": "other", "start_date": yesterday}).status_code, 400)
        self.assertEqual(self.client.post(reverse("my-leave"), {"kind": "other", "start_date": TODAY.isoformat()}).status_code, 201)

    def test_a_request_cannot_cross_into_the_next_leave_year(self):
        boundary = (day(40).replace(day=1))
        with override_settings(LEAVE_YEAR_START_MONTH=boundary.month):
            self.login(self.emp_a.user)
            response = self.client.post(reverse("my-leave"), {
                "kind": "other", "start_date": (boundary - timedelta(days=2)).isoformat(),
                "end_date": (boundary + timedelta(days=1)).isoformat(),
            })
        self.assertEqual(response.status_code, 400)
        self.assertIn("end_date", response.data)

    def test_leave_has_to_fall_within_employment(self):
        self.emp_a.end_date = day(2)
        self.emp_a.save()
        self.assertEqual(self.ask(self.emp_a.user, n=1, to=4).status_code, 400)
        self.assertEqual(self.ask(self.emp_a.user, n=1, to=2).status_code, 201)

    def test_dates_cannot_overlap_until_the_other_request_is_closed(self):
        first = self.ask(self.emp_a.user, n=0, to=2).data["id"]
        self.assertEqual(self.ask(self.emp_a.user, n=2, to=4).status_code, 400)
        self.assertEqual(self.ask(self.emp_a.user, n=3, to=4).status_code, 201)  # next to it is fine
        self.decide(self.manager_a, "reject", first, reason="Busy")
        self.assertEqual(self.ask(self.emp_a.user, n=0, to=2).status_code, 201)

    def test_annual_leave_cannot_exceed_the_allowance_and_waiting_requests_count(self):
        self.set_allowance(self.emp_a, 5)
        self.assertEqual(self.ask(self.emp_a.user, n=0, to=2).status_code, 201)  # 3 of 5
        over = self.ask(self.emp_a.user, n=7, to=9)
        self.assertEqual(over.status_code, 400)
        self.assertIn("2 days", self.errors(over))
        self.assertEqual(self.ask(self.emp_a.user, n=7, to=8).status_code, 201)  # exactly the 2 left

    def test_unpaid_and_other_leave_do_not_use_the_allowance(self):
        self.set_allowance(self.emp_a, 0)
        self.assertEqual(self.ask(self.emp_a.user, kind="unpaid", n=0, to=3).status_code, 201)
        self.assertEqual(self.ask(self.emp_a.user, kind="other", n=5).status_code, 201)
        self.assertEqual(self.ask(self.emp_a.user, kind="annual", n=9).status_code, 400)

    def test_a_half_day_counts_as_half(self):
        self.assertEqual(self.ask(self.emp_a.user, n=0, half_day="pm").data["days_requested"], "0.5")

    def test_only_people_with_an_employee_profile_can_ask(self):
        response = self.ask(self.owner, n=0)
        self.assertEqual(response.status_code, 404)

    def test_the_default_allowance_applies_when_hr_has_not_set_one(self):
        LeaveAllowance.objects.all().delete()
        self.login(self.emp_b.user)
        data = self.client.get(reverse("my-leave-balance")).data
        first, last = leave_year(TODAY)
        self.assertEqual(Decimal(str(data["allowance"])), default_allowance(self.emp_b, first, last))
        self.assertEqual(data["year_start"], first.isoformat())


class SickLeaveTests(LeaveBase):
    def sick(self, user=None, start=0, end=None):
        self.login(user or self.emp_a.user)
        body = {"kind": "sick", "start_date": (TODAY + timedelta(days=start)).isoformat()}
        if end is not None:
            body["end_date"] = (TODAY + timedelta(days=end)).isoformat()
        return self.client.post(reverse("my-leave"), body)

    def test_sickness_is_recorded_at_once_and_the_manager_hears_about_shifts_that_need_cover(self):
        self.shift(self.emp_a, 0)
        response = self.sick(start=0, end=(day(0) - TODAY).days)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "approved")
        self.assertIsNone(response.data["days_charged"])
        self.assertEqual(queued("sick_reported"), ["mgr-a@example.com"])
        send_everything()
        body = inbox("mgr-a@example.com")[0].body
        self.assertIn("reported sick", body)
        self.assertIn(f"{day(0):%a %d %b}, 09:00-17:00 at Shop A", body)
        self.assertEqual(inbox("emp-a@example.com"), [])

    def test_sickness_does_not_take_days_off_the_allowance(self):
        self.sick(start=-2, end=0)
        self.login(self.emp_a.user)
        self.assertEqual(Decimal(str(self.client.get(reverse("my-leave-balance")).data["remaining"])), Decimal("10"))

    def test_sickness_cannot_be_reported_for_the_future_or_long_ago(self):
        self.assertEqual(self.sick(start=1).status_code, 400)
        self.assertEqual(self.sick(start=-15).status_code, 400)
        self.assertEqual(self.sick(start=-14).status_code, 201)

    def test_sickness_cannot_be_reported_far_ahead(self):
        self.assertEqual(self.sick(start=0, end=15).status_code, 400)
        self.assertEqual(self.sick(start=0, end=14).status_code, 201)

    def test_the_employee_cannot_cancel_a_sickness_report_and_managers_never_need_to_approve_it(self):
        leave = self.sick().data["id"]
        self.login(self.emp_a.user)
        refused = self.client.post(reverse("my-leave-cancel", args=[leave]))
        self.assertEqual(refused.status_code, 400)
        self.assertIn("Sickness reports can't be cancelled", self.errors(refused))
        self.assertEqual(self.decide(self.manager_a, "approve", leave).status_code, 400)


class DecidingTests(LeaveBase):
    def test_a_manager_approves_and_the_days_come_off_the_allowance(self):
        leave = self.ask(self.emp_a.user, n=0, to=2).data["id"]
        response = self.decide(self.manager_a, "approve", leave)
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.data["status"], response.data["days_charged"]), ("approved", "3.0"))
        self.assertEqual(response.data["reviewed_by"], self.manager_a.email)
        self.login(self.emp_a.user)
        balance = self.client.get(reverse("my-leave-balance")).data
        self.assertEqual((Decimal(str(balance["used"])), Decimal(str(balance["remaining"]))), (Decimal("3"), Decimal("7")))
        self.assertEqual(queued("leave_decided"), ["emp-a@example.com"])  # not the manager who decided
        send_everything()
        mail_to_employee = inbox("emp-a@example.com")
        self.assertEqual([m.subject for m in mail_to_employee], ["Your leave was approved"])
        self.assertIn("Taken off your annual leave: 3 day(s)", mail_to_employee[0].body)

    def test_the_manager_can_charge_fewer_days_but_not_more_or_odd_amounts(self):
        leave = self.ask(self.emp_a.user, n=0, to=4).data["id"]  # 5 days asked
        for bad in ("0", "5.5", "1.3", "-1"):
            self.assertEqual(self.decide(self.manager_a, "approve", leave, days_charged=bad).status_code, 400, bad)
        response = self.decide(self.manager_a, "approve", leave, days_charged="3.5")
        self.assertEqual(response.data["days_charged"], "3.5")
        self.login(self.emp_a.user)
        balance = self.client.get(reverse("my-leave-balance")).data
        self.assertEqual((Decimal(str(balance["used"])), Decimal(str(balance["remaining"]))), (Decimal("3.5"), Decimal("6.5")))

    def test_only_annual_leave_has_charged_days(self):
        leave = self.ask(self.emp_a.user, kind="unpaid", n=0, to=1).data["id"]
        self.assertEqual(self.decide(self.manager_a, "approve", leave, days_charged="1").status_code, 400)
        response = self.decide(self.manager_a, "approve", leave)
        self.assertEqual((response.status_code, response.data["days_charged"]), (200, None))

    def test_approval_checks_the_allowance_again(self):
        leave = self.ask(self.emp_a.user, n=0, to=3).data["id"]  # 4 of 10
        self.set_allowance(self.emp_a, 3)  # HR lowers it afterwards
        response = self.decide(self.manager_a, "approve", leave)
        self.assertEqual(response.status_code, 400)
        self.assertIn("3 days", self.errors(response))
        self.assertEqual(LeaveRequest.objects.get(pk=leave).status, LeaveStatus.PENDING)

    def test_other_requests_waiting_do_not_block_an_approval(self):
        self.set_allowance(self.emp_a, 4)
        first = self.ask(self.emp_a.user, n=0, to=1).data["id"]
        second = self.ask(self.emp_a.user, n=5, to=6).data["id"]
        self.assertEqual(self.decide(self.manager_a, "approve", first).status_code, 200)
        self.assertEqual(self.decide(self.manager_a, "approve", second).status_code, 200)

    def test_a_manager_cannot_approve_while_the_person_is_still_on_the_rota(self):
        self.shift(self.emp_a, 1)
        leave = self.ask(self.emp_a.user, n=0, to=2).data["id"]
        response = self.decide(self.manager_a, "approve", leave)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.codes(response), ["has_shifts"])
        self.assertIn(f"{day(1):%a %d %b} at Shop A", response.data["blocks"][0]["message"])
        rota_services.cancel_shift(shift=Shift_on(self.emp_a, day(1)), by=self.hr)
        self.assertEqual(self.decide(self.manager_a, "approve", leave).status_code, 200)

    def test_a_half_day_of_leave_can_be_approved_even_with_a_shift_that_day(self):
        self.shift(self.emp_a, 4)
        half = self.ask(self.emp_a.user, n=4, half_day="am").data["id"]
        self.assertEqual(self.decide(self.manager_a, "approve", half).status_code, 200)

    def test_nobody_decides_twice_or_after_the_leave_started(self):
        leave = self.ask(self.emp_a.user, n=0).data["id"]
        self.assertEqual(self.decide(self.manager_a, "approve", leave).status_code, 200)
        self.assertEqual(self.decide(self.manager_a, "approve", leave).status_code, 400)
        self.assertEqual(self.decide(self.manager_a, "reject", leave, reason="x").status_code, 400)
        late = self.ask(self.emp_a.user, n=3).data["id"]
        with mock.patch("apps.leave.services.today", return_value=day(4)):
            self.assertEqual(self.decide(self.manager_a, "approve", late).status_code, 400)

    def test_the_other_stores_manager_and_employees_cannot_decide(self):
        leave = self.ask(self.emp_a.user, n=0).data["id"]
        self.assertEqual(self.decide(self.manager_b, "approve", leave).status_code, 404)
        self.assertEqual(self.decide(self.manager_b, "reject", leave, reason="x").status_code, 404)
        self.assertEqual(self.decide(self.emp_b.user, "approve", leave).status_code, 403)
        self.assertEqual(self.decide(self.emp_a.user, "approve", leave).status_code, 403)
        self.assertEqual(LeaveRequest.objects.get(pk=leave).status, LeaveStatus.PENDING)

    def test_a_managers_leave_is_decided_by_owner_or_hr_only(self):
        leave = self.ask(self.manager_a, n=0).data["id"]
        self.assertEqual(self.decide(self.manager_a, "approve", leave).status_code, 404)  # not even their own
        self.assertEqual(self.decide(self.manager_b, "approve", leave).status_code, 404)
        self.assertEqual(self.decide(self.hr, "approve", leave, days_charged=None).status_code, 200)

    def test_even_if_a_manager_reaches_the_service_they_cannot_decide_a_managers_leave(self):
        leave = LeaveRequest.objects.get(pk=self.ask(self.manager_a, n=0).data["id"])
        from rest_framework.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            services.approve(leave=leave, by=self.manager_b)
        with self.assertRaises(ValidationError):
            services.approve(leave=leave, by=self.manager_a)  # their own

    def test_an_employee_who_reaches_the_service_cannot_decide(self):
        from rest_framework.exceptions import PermissionDenied

        leave = LeaveRequest.objects.get(pk=self.ask(self.emp_a.user, n=0).data["id"])
        with self.assertRaises(PermissionDenied):
            services.approve(leave=leave, by=self.emp_b.user)
        with self.assertRaises(PermissionDenied):
            services.reject(leave=leave, by=self.emp_b.user, reason="x")

    def test_nobody_decides_their_own_leave_even_hr(self):
        hr_profile = employee_services.create_employee(
            user=self.hr, home_store=self.store_a, start_date=TODAY - timedelta(days=100), created_by=self.owner,
        )
        self.set_allowance(hr_profile, 5)
        leave = self.ask(self.hr, n=0).data["id"]
        response = self.decide(self.hr, "approve", leave)
        self.assertEqual(response.status_code, 400)
        self.assertIn("own", self.errors(response))
        self.assertEqual(self.decide(self.owner, "approve", leave).status_code, 200)

    def test_rejecting_needs_a_reason_and_the_employee_sees_it(self):
        leave = self.ask(self.emp_a.user, n=0).data["id"]
        self.assertEqual(self.decide(self.manager_a, "reject", leave, reason="  ").status_code, 400)
        response = self.decide(self.manager_a, "reject", leave, reason="We are short that week")
        self.assertEqual((response.status_code, response.data["status"]), (200, "rejected"))
        self.login(self.emp_a.user)
        mine = self.client.get(reverse("my-leave")).data[0]
        self.assertEqual((mine["status"], mine["decision_note"]), ("rejected", "We are short that week"))
        send_everything()
        self.assertIn("We are short that week", inbox("emp-a@example.com")[0].body)
        self.assertEqual(inbox("emp-a@example.com")[0].subject, "Your leave request was not approved")


def Shift_on(employee, when):
    from apps.rota.models import Shift

    return Shift.objects.get(employee=employee, date=when)


class CancellingTests(LeaveBase):
    def cancel(self, user, pk):
        self.login(user)
        return self.client.post(reverse("my-leave-cancel", args=[pk]))

    def test_cancelling_a_waiting_request_tells_nobody_and_frees_the_allowance(self):
        leave = self.ask(self.emp_a.user, n=0, to=4).data["id"]
        self.assertEqual(self.cancel(self.emp_a.user, leave).data["status"], "cancelled")
        self.assertEqual(Notification.objects.filter(kind="leave_cancelled_by_employee").count(), 0)
        self.login(self.emp_a.user)
        self.assertEqual(Decimal(str(self.client.get(reverse("my-leave-balance")).data["remaining"])), Decimal("10"))

    def test_cancelling_approved_future_leave_returns_the_days_and_tells_the_managers(self):
        leave = self.approved(n=0, to=2)
        self.assertEqual(self.cancel(self.emp_a.user, leave.pk).data["status"], "cancelled")
        self.login(self.emp_a.user)
        self.assertEqual(Decimal(str(self.client.get(reverse("my-leave-balance")).data["used"])), Decimal("0"))
        self.assertEqual(queued("leave_cancelled_by_employee"), ["mgr-a@example.com"])

    def test_leave_that_has_started_cannot_be_cancelled_by_the_employee(self):
        leave = self.approved(n=0)
        with mock.patch("apps.leave.services.today", return_value=day(0)):
            self.assertEqual(self.cancel(self.emp_a.user, leave.pk).status_code, 400)
        self.assertEqual(LeaveRequest.objects.get(pk=leave.pk).status, LeaveStatus.APPROVED)

    def test_the_service_refuses_to_cancel_someone_elses_leave(self):
        from rest_framework.exceptions import PermissionDenied

        leave = LeaveRequest.objects.get(pk=self.ask(self.emp_a.user, n=0).data["id"])
        with self.assertRaises(PermissionDenied):
            services.cancel_own(leave=leave, user=self.emp_b.user)
        self.assertEqual(LeaveRequest.objects.get(pk=leave.pk).status, LeaveStatus.PENDING)

    def test_a_closed_request_and_someone_elses_request_cannot_be_cancelled(self):
        leave = self.ask(self.emp_a.user, n=0).data["id"]
        self.cancel(self.emp_a.user, leave)
        self.assertEqual(self.cancel(self.emp_a.user, leave).status_code, 400)
        self.assertEqual(self.cancel(self.emp_b.user, leave).status_code, 404)

    def test_a_manager_cancels_with_a_reason_and_the_employee_is_told(self):
        leave = self.approved(n=0, to=1)
        self.assertEqual(self.decide(self.manager_a, "cancel", leave.pk, reason="").status_code, 400)
        response = self.decide(self.manager_a, "cancel", leave.pk, reason="Stock count that week")
        self.assertEqual((response.status_code, response.data["status"]), (200, "cancelled"))
        send_everything()
        self.assertIn("Stock count that week", inbox("emp-a@example.com")[-1].body)
        self.assertEqual(inbox("emp-a@example.com")[-1].subject, "Your leave was cancelled")

    def test_only_hr_or_owner_can_cancel_leave_that_has_started(self):
        leave = self.approved(n=0)
        with mock.patch("apps.leave.services.today", return_value=day(0)):
            self.assertEqual(self.decide(self.manager_a, "cancel", leave.pk, reason="x").status_code, 400)
            self.assertEqual(self.decide(self.hr, "cancel", leave.pk, reason="Entered by mistake").status_code, 200)

    def test_the_other_stores_manager_cannot_cancel(self):
        leave = self.approved(n=0)
        self.assertEqual(self.decide(self.manager_b, "cancel", leave.pk, reason="x").status_code, 404)


class RotaInteractionTests(LeaveBase):
    def add(self, n, employee=None):
        return super().add(self.manager_a, self.week_a, employee or self.emp_a, n=n)

    def test_approved_leave_blocks_a_shift_on_those_days_only(self):
        self.approved(n=1, to=2)
        blocked = self.add(1)
        self.assertEqual((blocked.status_code, self.codes(blocked)), (400, ["on_leave"]))
        self.assertEqual(self.codes(self.add(2)), ["on_leave"])
        self.assertEqual(self.add(0).status_code, 201)
        self.assertEqual(self.add(3).status_code, 201)

    def test_the_rota_never_says_why_someone_is_off(self):
        self.approved(n=1, kind="unpaid")
        self.login(self.emp_a.user)
        self.client.post(reverse("my-leave"), {"kind": "sick", "start_date": TODAY.isoformat(), "end_date": day(0).isoformat()})
        for n in (0, 1):
            message = [b["message"] for b in self.add(n).data["blocks"] if b["code"] == "on_leave"][0]
            self.assertEqual(message, "This person is on approved leave that day.")

    def test_waiting_rejected_and_cancelled_leave_does_not_block(self):
        waiting = self.ask(self.emp_a.user, n=1).data["id"]
        self.assertEqual(self.add(1).status_code, 201)
        Shift_on(self.emp_a, day(1)).delete()
        self.decide(self.manager_a, "reject", waiting, reason="no")
        self.assertEqual(self.add(1).status_code, 201)
        Shift_on(self.emp_a, day(1)).delete()
        approved = self.approved(n=2)
        self.decide(self.manager_a, "cancel", approved.pk, reason="changed")
        self.assertEqual(self.add(2).status_code, 201)

    def test_a_half_day_of_leave_is_a_warning_not_a_block(self):
        self.approved(n=1, half_day="am")
        response = self.add(1)
        self.assertEqual(response.status_code, 201)
        self.assertIn("half_day_leave", self.warning_codes(response))

    def test_sickness_blocks_new_shifts_but_leaves_existing_ones_for_the_manager_to_move(self):
        self.shift(self.emp_a, 0)
        self.login(self.emp_a.user)
        self.client.post(reverse("my-leave"), {"kind": "sick", "start_date": TODAY.isoformat(), "end_date": day(0).isoformat()})
        self.assertIn("on_leave", self.codes(self.add(0, self.emp_a)))
        self.assertEqual(Shift_on(self.emp_a, day(0)).status, "scheduled")  # the manager has to move it


class ViewingTests(LeaveBase):
    def test_managers_see_their_own_stores_staff_but_never_other_managers_leave(self):
        mine = self.ask(self.emp_a.user, n=0).data["id"]
        theirs = self.ask(self.emp_b.user, n=0).data["id"]
        managers = self.ask(self.manager_a, n=2).data["id"]
        self.login(self.manager_a)
        ids = [row["id"] for row in self.client.get(reverse("leave-queue")).data]
        self.assertEqual(ids, [mine])
        self.login(self.hr)
        ids = sorted(row["id"] for row in self.client.get(reverse("leave-queue")).data)
        self.assertEqual(ids, sorted([mine, theirs, managers]))

    def test_the_queue_defaults_to_waiting_and_can_be_filtered(self):
        waiting = self.ask(self.emp_a.user, n=0).data["id"]
        done = self.ask(self.emp_a.user, n=3).data["id"]
        self.decide(self.manager_a, "approve", done)
        self.login(self.manager_a)
        self.assertEqual([r["id"] for r in self.client.get(reverse("leave-queue")).data], [waiting])
        self.assertEqual([r["id"] for r in self.client.get(reverse("leave-queue"), {"status": "approved"}).data], [done])
        self.assertEqual(len(self.client.get(reverse("leave-queue"), {"status": "all"}).data), 2)
        self.assertEqual(self.client.get(reverse("leave-queue"), {"status": "nonsense"}).status_code, 400)

    def test_the_queue_shows_how_much_annual_leave_is_left_to_decide_with(self):
        self.ask(self.emp_a.user, n=0, to=1)
        self.login(self.manager_a)
        self.assertEqual(Decimal(str(self.client.get(reverse("leave-queue")).data[0]["annual_leave_left"])), Decimal("10"))

    def test_employees_cannot_open_the_manager_pages(self):
        self.login(self.emp_a.user)
        for name in ("leave-queue", "leave-calendar"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)

    def test_employees_only_see_their_own_leave(self):
        self.ask(self.emp_a.user, n=0)
        self.ask(self.emp_b.user, n=0)
        self.login(self.emp_a.user)
        rows = self.client.get(reverse("my-leave")).data
        self.assertEqual(len(rows), 1)
        self.assertNotIn("employee", rows[0])

    def test_the_calendar_shows_approved_leave_without_saying_why(self):
        approved = self.approved(n=1, to=2)
        self.ask(self.emp_a.user, n=5)  # still waiting: not shown
        self.ask(self.emp_b.user, n=1)  # another store: not shown
        self.login(self.manager_a)
        rows = self.client.get(reverse("leave-calendar"), {"start": day(0).isoformat(), "end": day(6).isoformat()}).data
        self.assertEqual([r["id"] for r in rows], [approved.pk])
        self.assertNotIn("kind", rows[0])
        self.assertEqual(self.client.get(reverse("leave-calendar"), {"start": day(3).isoformat(), "end": day(6).isoformat()}).data, [])

    def test_the_calendar_checks_its_dates(self):
        self.login(self.manager_a)
        self.assertEqual(self.client.get(reverse("leave-calendar")).status_code, 400)
        self.assertEqual(self.client.get(reverse("leave-calendar"), {"start": day(5).isoformat(), "end": day(1).isoformat()}).status_code, 400)
        self.assertEqual(self.client.get(reverse("leave-calendar"), {"start": day(0).isoformat(), "end": day(90).isoformat()}).status_code, 400)


class AllowanceTests(LeaveBase):
    def put(self, user, employee, **body):
        self.login(user)
        return self.client.put(reverse("employee-leave-allowance", args=[employee.pk]), body)

    def test_hr_sets_the_allowance_and_the_balance_follows(self):
        response = self.put(self.hr, self.emp_b, days="21.5", date=day(0).isoformat())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Decimal(str(response.data["allowance"])), Decimal("21.5"))
        self.assertEqual(self.put(self.owner, self.emp_b, days="22", date=day(0).isoformat()).status_code, 200)
        self.assertEqual(LeaveAllowance.objects.filter(employee=self.emp_b).count(), 1)  # updated, not duplicated
        self.login(self.emp_b.user)
        self.assertEqual(Decimal(str(self.client.get(reverse("my-leave-balance")).data["allowance"])), Decimal("22"))

    def test_odd_amounts_are_refused(self):
        for days in ("-1", "10.3", "400"):
            self.assertEqual(self.put(self.hr, self.emp_b, days=days).status_code, 400, days)

    def test_managers_and_employees_cannot_set_it_but_can_not_read_it_either(self):
        for user in (self.manager_a, self.emp_a.user):
            self.assertEqual(self.put(user, self.emp_a, days="30").status_code, 403)
            self.assertEqual(self.client.get(reverse("employee-leave-allowance", args=[self.emp_a.pk])).status_code, 403)
        self.assertEqual(Decimal(str(services.allowance_for(self.emp_a, day(0)))), Decimal("10"))

    def test_each_leave_year_has_its_own_allowance(self):
        first, last = leave_year(day(0))
        next_year = last + timedelta(days=1)
        self.assertEqual(self.put(self.hr, self.emp_a, days="3", date=next_year.isoformat()).status_code, 200)
        self.assertEqual(services.allowance_for(self.emp_a, day(0)), Decimal("10"))
        self.assertEqual(services.allowance_for(self.emp_a, next_year), Decimal("3"))


class EmailTests(LeaveBase):
    def test_emails_say_what_is_needed_and_nothing_about_immigration(self):
        leave = self.ask(self.emp_a.user, n=0, to=1, note="Visa appointment\nat the Home Office").data["id"]
        self.decide(self.manager_a, "approve", leave)
        second = self.ask(self.emp_a.user, n=5).data["id"]
        self.decide(self.manager_a, "reject", second, reason="Busy")
        send_everything()
        manager_mail = inbox("mgr-a@example.com")[0]
        self.assertIn("asked for annual leave", manager_mail.body)
        self.assertIn(f"{day(0):%a %d %b} to {day(1):%a %d %b} (2 day(s))", manager_mail.body)
        self.assertIn("Note: Visa appointment at the Home Office", manager_mail.body)  # their own words, one line
        for message in inbox("emp-a@example.com"):
            self.assertNotIn("Home Office", message.body)
        self.assertEqual(manager_mail.subject, "A leave request needs a decision")
        for message in inbox("emp-a@example.com") + [manager_mail]:
            self.assertNotIn("Visa appointment", message.subject)

    def test_sickness_is_never_mentioned_to_the_employee_or_on_the_calendar(self):
        self.login(self.emp_a.user)
        self.client.post(reverse("my-leave"), {"kind": "sick", "start_date": TODAY.isoformat()})
        send_everything()
        self.assertEqual(inbox("emp-a@example.com"), [])
        self.assertEqual(inbox("mgr-b@example.com"), [])
        self.login(self.manager_a)
        rows = self.client.get(reverse("leave-calendar"), {"start": TODAY.isoformat(), "end": TODAY.isoformat()}).data
        self.assertNotIn("kind", rows[0])

    def test_a_half_day_is_described_in_the_email(self):
        self.ask(self.emp_a.user, n=0, half_day="pm")
        send_everything()
        self.assertIn(f"{day(0):%a %d %b} (afternoon only) (0.5 day(s))", inbox("mgr-a@example.com")[0].body)

    def test_a_store_without_managers_falls_back_to_owner_and_hr(self):
        self.store_a.managers.clear()
        self.ask(self.emp_a.user, n=0)
        self.assertEqual(queued("leave_requested"), ["hr@example.com", "owner@example.com"])

    def test_someone_with_no_store_on_those_dates_still_reaches_owner_and_hr(self):
        from apps.employees.models import StoreAssignment

        StoreAssignment.objects.filter(employee=self.emp_a).update(valid_to=TODAY - timedelta(days=1))
        self.assertEqual(self.ask(self.emp_a.user, n=0).status_code, 201)
        self.assertEqual(queued("leave_requested"), ["hr@example.com", "owner@example.com"])

    def test_cover_staff_also_reach_the_managers_of_the_store_they_cover(self):
        self.cover(self.emp_a, self.store_b, first=0, last=6)
        # approve the cover request from both sides so it is active
        from apps.employees.models import StoreAssignment

        StoreAssignment.objects.filter(employee=self.emp_a, kind="cover").update(status="approved")
        self.ask(self.emp_a.user, n=1)
        self.assertEqual(queued("leave_requested"), ["mgr-a@example.com", "mgr-b@example.com"])
