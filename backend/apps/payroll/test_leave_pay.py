from datetime import date, timedelta
from decimal import Decimal

from django.urls import reverse
from rest_framework.exceptions import ValidationError

from apps.leave.models import LeaveKind, LeaveRequest, LeaveStatus
from apps.payroll import services
from apps.payroll.models import PayDetails, Payslip, PayslipTemplate, RunStatus
from apps.payroll.test_payroll_api import ApiBase
from apps.payroll.test_payruns import MON, NEXT, PayBase


def leave(employee, start, end, kind=LeaveKind.ANNUAL, status=LeaveStatus.APPROVED, half="", charged=None, requested=None):
    days = requested if requested is not None else Decimal("0.5") if half else Decimal((end - start).days + 1)
    return LeaveRequest.objects.create(
        employee=employee, kind=kind, start_date=start, end_date=end, half_day=half, days_requested=days,
        days_charged=charged if charged is not None else (days if kind == LeaveKind.ANNUAL else None), status=status,
    )


class LeavePayBase(PayBase):
    def typical(self, hours="8"):
        services.set_pay_details(self.hr, self.worker, {"typical_day_hours": Decimal(hours)})

    def approve(self, run):
        for slip in run.payslips.all():
            services.set_deductions(self.hr, slip, {}, True)
        return services.approve_run(self.hr, run, PayslipTemplate.current())


class HolidayPayTests(LeavePayBase):
    def test_leave_days_are_paid_at_the_typical_day_until_there_is_pay_history(self):
        self.typical("8")
        leave(self.worker, MON + timedelta(days=2), MON + timedelta(days=3))
        slip = self.slip(self.run_for())
        self.assertEqual(slip.earnings, [{"label": "Holiday pay", "minutes": 960, "rate": "12.00", "amount": "192.00"}])
        self.assertEqual((slip.gross_pay, slip.paid_minutes), (Decimal("192.00"), 0))
        self.assertEqual(slip.leave_dates, {str(LeaveRequest.objects.get().pk): ["2026-10-21", "2026-10-22"]})

    def test_later_it_is_paid_at_the_average_paid_day_of_the_last_52_weeks(self):
        self.sample_week()  # 2330 minutes over 5 days = 466 minutes a day
        self.approve(self.run_for())
        leave(self.worker, NEXT + timedelta(days=2), NEXT + timedelta(days=3))
        slip = self.slip(self.run_for(NEXT))
        self.assertEqual(slip.earnings, [{"label": "Holiday pay", "minutes": 932, "rate": "12.00", "amount": "186.40"}])

    def test_history_beats_the_typical_day_and_history_older_than_52_weeks_does_not_count(self):
        self.typical("10")
        self.sample_week()
        run = self.run_for()
        self.approve(run)
        leave(self.worker, NEXT, NEXT)
        self.assertEqual(self.slip(self.run_for(NEXT)).earnings[0]["minutes"], 466)
        LeaveRequest.objects.update(status=LeaveStatus.CANCELLED)
        far = NEXT + timedelta(days=7 * 60)  # well over a year on: the history has aged out
        leave(self.worker, far, far)
        Payslip.objects.filter(pay_run__period_start=NEXT).delete()
        from apps.payroll.models import PayRun
        PayRun.objects.filter(period_start=NEXT).delete()
        later = PayRun.objects.create(period_start=far, period_end=far + timedelta(days=6), pay_date=far + timedelta(days=10))
        services.fill_run(later)
        self.assertEqual(self.slip(later).earnings[0]["minutes"], 600)

    def test_a_half_day_a_charged_fewer_days_and_the_rate_on_the_day(self):
        self.typical("8")
        services.add_rate(self.hr, self.worker, Decimal("13.00"), MON + timedelta(days=4))
        leave(self.worker, MON, MON, half="am")                       # 0.5 day at 12.00
        leave(self.worker, MON + timedelta(days=3), MON + timedelta(days=6), charged=Decimal("3"))  # 4 dates, 3 days charged
        slip = self.slip(self.run_for())
        by_rate = {(e["rate"]): (e["minutes"], e["amount"]) for e in slip.earnings}
        # 12.00: 0.5 day on Mon + 0.75 x 1 date (Thu) = 1.25 days = 600 minutes; 13.00: 0.75 x 3 dates = 2.25 days = 1080
        self.assertEqual(by_rate["12.00"], (600, "120.00"))
        self.assertEqual(by_rate["13.00"], (1080, "234.00"))

    def test_two_records_on_one_day_count_as_one_day_when_averaging(self):
        self.rec(0, "09:00", "13:00")
        self.rec(0, "14:00", "18:00")   # a split shift: 8 hours over one day
        self.rec(1, "09:00", "13:00")   # 4 hours over one day
        run = self.run_for()
        self.approve(run)                # 720 minutes over 2 days = 360 a day
        leave(self.worker, NEXT, NEXT)
        self.assertEqual(self.slip(self.run_for(NEXT)).earnings[0]["minutes"], 360)

    def test_leave_that_starts_before_the_first_run_is_paid_only_from_the_first_run(self):
        self.typical("8")
        leave(self.worker, MON - timedelta(days=2), MON + timedelta(days=1))   # 2 days before, 2 days in the week
        slip = self.slip(self.run_for())
        self.assertEqual(slip.earnings[0]["minutes"], 960)
        self.assertEqual(slip.leave_dates, {str(LeaveRequest.objects.get().pk): ["2026-10-19", "2026-10-20"]})

    def test_annual_leave_is_not_sick_leave(self):
        self.typical("8")
        leave(self.worker, MON, MON)
        slip = self.slip(self.run_for())
        self.assertEqual(slip.sick_dates, {})

    def test_only_approved_annual_leave_is_paid(self):
        self.typical("8")
        for kind, status in [(LeaveKind.UNPAID, LeaveStatus.APPROVED), (LeaveKind.OTHER, LeaveStatus.APPROVED),
                             (LeaveKind.ANNUAL, LeaveStatus.PENDING), (LeaveKind.ANNUAL, LeaveStatus.REJECTED),
                             (LeaveKind.ANNUAL, LeaveStatus.CANCELLED)]:
            leave(self.worker, MON, MON, kind=kind, status=status)
        self.assertEqual(self.run_for().payslips.count(), 0)

    def test_leave_that_spans_two_weeks_is_paid_in_each_week_for_its_own_days(self):
        self.typical("8")
        leave(self.worker, MON + timedelta(days=6), NEXT)  # Sunday and the Monday after
        first = self.run_for()
        self.assertEqual(self.slip(first).earnings[0]["minutes"], 480)
        self.approve(first)
        second = self.run_for(NEXT)
        self.assertEqual(self.slip(second).earnings[0]["minutes"], 480)
        self.approve(second)
        self.assertEqual(self.run_for(NEXT + timedelta(days=7)).payslips.count(), 0)

    def test_leave_approved_after_its_week_was_paid_is_picked_up_in_the_next_run(self):
        self.typical("8")
        self.rec(0, "09:00", "17:00")
        self.approve(self.run_for())
        leave(self.worker, MON + timedelta(days=2), MON + timedelta(days=2))
        slip = self.slip(self.run_for(NEXT))
        self.assertEqual((slip.earnings[0]["minutes"], slip.earnings[0]["label"]), (480, "Holiday pay"))

    def test_leave_before_the_first_run_is_treated_as_already_paid(self):
        self.typical("8")
        self.rec(0, "09:00", "17:00")
        leave(self.worker, MON - timedelta(days=3), MON - timedelta(days=1))
        slip = self.slip(self.run_for())
        self.assertEqual([e["label"] for e in slip.earnings], ["Basic pay"])

    def test_nothing_to_average_and_no_typical_day_holds_the_leave_and_stops_approval(self):
        leave(self.worker, MON, MON)
        run = self.run_for()
        slip = self.slip(run)
        self.assertEqual((slip.gross_pay, [h["reason"] for h in slip.held]), (Decimal("0"), ["no_typical_day"]))
        self.assertTrue(any("typical day" in p for p in services.blockers(run)))
        self.typical("8")
        services.fill_run(run)
        self.assertEqual(self.slip(run).gross_pay, Decimal("96.00"))

    def test_leave_on_a_day_with_no_rate_is_held(self):
        self.typical("8")
        leave(self.emp_b, MON, MON)
        services.set_pay_details(self.hr, self.emp_b, {"typical_day_hours": Decimal("8")})
        run = self.run_for()
        self.assertEqual([h["reason"] for h in self.slip(run, self.emp_b).held], ["no_rate"])
        self.assertTrue(any("no hourly rate" in p for p in services.blockers(run)))

    def test_holiday_pay_in_the_week_is_added_to_gross_and_the_hours_that_set_the_average_exclude_it(self):
        self.typical("8")
        self.rec(0, "09:00", "17:00")
        leave(self.worker, MON + timedelta(days=1), MON + timedelta(days=1))
        run = self.run_for()
        slip = self.slip(run)
        self.assertEqual(slip.gross_pay, Decimal("96.00") + Decimal("96.00"))
        self.assertEqual(slip.paid_minutes, 480)
        self.approve(run)
        leave(self.worker, NEXT, NEXT)
        self.assertEqual(self.slip(self.run_for(NEXT)).earnings[0]["minutes"], 480)


class TypicalDayAndBirthdayInputTests(LeavePayBase):
    def test_typical_day_limits(self):
        for bad in ("0", "0.49", "16.01", "-1"):
            with self.assertRaises(ValidationError, msg=bad):
                services.set_pay_details(self.hr, self.worker, {"typical_day_hours": Decimal(bad)})
        services.set_pay_details(self.hr, self.worker, {"typical_day_hours": Decimal("7.5")})
        self.assertEqual(PayDetails.objects.get().typical_day_minutes, 450)
        services.set_pay_details(self.hr, self.worker, {"typical_day_hours": None})
        self.assertIsNone(PayDetails.objects.get().typical_day_minutes)

    def test_date_of_birth_is_stored_encrypted_and_shown_only_as_its_year(self):
        from django.db import connection

        services.set_pay_details(self.hr, self.worker, {"date_of_birth": date(2000, 3, 3)})
        with connection.cursor() as cursor:
            cursor.execute("select date_of_birth_encrypted from payroll_paydetails")
            self.assertNotIn("2000", cursor.fetchone()[0])
        shown = services.masked_details(self.worker)
        self.assertEqual((shown["date_of_birth"], shown["has_date_of_birth"]), ("**/**/2000", True))
        services.set_pay_details(self.hr, self.worker, {"date_of_birth": None})
        self.assertFalse(services.masked_details(self.worker)["has_date_of_birth"])

    def test_a_date_of_birth_that_cannot_be_right_is_refused(self):
        today = date.today()
        for bad in (today, today + timedelta(days=1), today.replace(year=today.year - 101)):
            with self.assertRaises(ValidationError, msg=str(bad)):
                services.set_pay_details(self.hr, self.worker, {"date_of_birth": bad})
        services.set_pay_details(self.hr, self.worker, {"date_of_birth": today.replace(year=today.year - 100) + timedelta(days=1)})


class SickPayTests(LeavePayBase):
    def setUp(self):
        super().setUp()
        self.sick = leave(self.worker, MON + timedelta(days=1), MON + timedelta(days=2), kind=LeaveKind.SICK)

    def test_sick_days_make_a_payslip_that_needs_the_sick_pay_entered_and_confirmed(self):
        run = self.run_for()
        slip = self.slip(run)
        self.assertEqual((slip.gross_pay, slip.sick_dates), (Decimal("0"), {str(self.sick.pk): ["2026-10-20", "2026-10-21"]}))
        self.assertTrue(any("sick pay" in p for p in services.blockers(run)))
        services.set_sick_pay(self.hr, slip, Decimal("0"))
        services.set_deductions(self.hr, slip, {}, True)
        self.assertEqual(services.blockers(run), [])

    def test_sick_pay_is_added_to_gross_as_its_own_line_and_changing_it_asks_for_the_tax_again(self):
        slip = self.slip(self.run_for())
        services.set_sick_pay(self.hr, slip, Decimal("41.08"))
        slip.refresh_from_db()
        self.assertEqual(slip.gross_pay, Decimal("41.08"))
        self.assertEqual(slip.earnings, [{"label": "Statutory sick pay", "minutes": 0, "rate": "", "amount": "41.08"}])
        services.set_deductions(self.hr, slip, {"income_tax": Decimal("1")}, True)
        services.set_sick_pay(self.hr, slip, Decimal("41.08"))  # same figure: still confirmed
        self.assertTrue(Payslip.objects.get().deductions_confirmed)
        services.set_sick_pay(self.hr, slip, Decimal("82.16"))
        slip.refresh_from_db()
        self.assertEqual((slip.gross_pay, slip.deductions_confirmed), (Decimal("82.16"), False))
        self.assertEqual(len(slip.earnings), 1)

    def test_sick_pay_sits_alongside_hours_worked(self):
        self.rec(0, "09:00", "17:00")
        slip = self.slip(self.run_for())
        services.set_sick_pay(self.hr, slip, Decimal("20"))
        slip.refresh_from_db()
        self.assertEqual(slip.gross_pay, Decimal("116.00"))
        self.assertEqual([e["label"] for e in slip.earnings], ["Basic pay", "Statutory sick pay"])

    def test_a_rebuild_keeps_the_sick_pay_that_was_entered(self):
        run = self.run_for()
        services.set_sick_pay(self.hr, self.slip(run), Decimal("30"))
        services.fill_run(run)
        again = self.slip(run)
        self.assertEqual((again.sick_pay, again.gross_pay, again.earnings[-1]["amount"]), (Decimal("30.00"), Decimal("30.00"), "30.00"))

    def test_sick_pay_is_refused_for_someone_who_was_not_sick_or_when_negative_or_too_small_for_the_deductions(self):
        other = self.rec(0, "09:00", "17:00", who=self.emp_b)
        services.add_rate(self.hr, self.emp_b, Decimal("11"), MON)
        services.set_pay_details(self.hr, self.emp_b, {"date_of_birth": date(2007, 6, 1)})
        run = self.run_for()
        with self.assertRaisesMessage(ValidationError, "no sick days"):
            services.set_sick_pay(self.hr, self.slip(run, self.emp_b), Decimal("5"))
        with self.assertRaisesMessage(ValidationError, "negative"):
            services.set_sick_pay(self.hr, self.slip(run), Decimal("-1"))
        slip = self.slip(run)
        services.set_sick_pay(self.hr, slip, Decimal("50"))
        services.set_deductions(self.hr, slip, {"income_tax": Decimal("40")}, True)
        with self.assertRaises(ValidationError):
            services.set_sick_pay(self.hr, slip, Decimal("10"))

    def test_sick_days_are_covered_once_and_the_payslip_freezes_when_approved(self):
        run = self.run_for()
        slip = self.slip(run)
        services.set_sick_pay(self.hr, slip, Decimal("10"))
        services.set_deductions(self.hr, slip, {}, True)
        services.approve_run(self.hr, run, PayslipTemplate.current())
        with self.assertRaises(ValidationError):
            services.set_sick_pay(self.hr, slip, Decimal("11"))
        self.assertEqual(self.run_for(NEXT).payslips.count(), 0)

    def test_sick_days_already_paid_in_another_run_stop_approval(self):
        run = self.run_for()
        slip = self.slip(run)
        services.set_sick_pay(self.hr, slip, Decimal("0"))
        services.set_deductions(self.hr, slip, {}, True)
        from apps.payroll.models import PayRun
        other = PayRun.objects.create(period_start=NEXT, period_end=NEXT + timedelta(days=6), pay_date=NEXT + timedelta(days=10), status=RunStatus.APPROVED)
        Payslip.objects.create(pay_run=other, employee=self.emp_b, number="x", sick_dates=slip.sick_dates)
        with self.assertRaisesMessage(ValidationError, "already paid"):
            services.approve_run(self.hr, run, PayslipTemplate.current())

    def test_only_approved_sick_leave_counts(self):
        LeaveRequest.objects.update(status=LeaveStatus.REJECTED)
        self.assertEqual(self.run_for().payslips.count(), 0)

    def test_sick_leave_is_never_paid_as_holiday(self):
        self.typical("8")
        slip = self.slip(self.run_for())
        self.assertEqual(slip.earnings, [])
        self.assertEqual(slip.leave_dates, {})


class SickPayEndpointTests(ApiBase):
    def test_owner_and_hr_set_sick_pay_and_nobody_else_can(self):
        leave(self.worker, MON, MON, kind=LeaveKind.SICK)
        run = self.run_for()
        slip = self.slip(run)
        url = reverse("payslip-sick-pay", args=[slip.pk])
        for user in (self.manager_a, self.worker.user):
            self.as_user(user)
            self.assertEqual(self.client.patch(url, {"sick_pay": "10"}).status_code, 403)
        self.as_user(self.hr)
        done = self.client.patch(url, {"sick_pay": "41.08"})
        self.assertEqual((done.status_code, done.json()["gross_pay"], done.json()["sick_pay"]), (200, "41.08", "41.08"))
        self.assertEqual(self.client.patch(url, {}).status_code, 400)
        self.assertEqual(self.client.patch(url, {"sick_pay": "x"}).status_code, 400)
        self.assertEqual(self.client.patch(reverse("payslip-sick-pay", args=[9999]), {"sick_pay": "1"}).status_code, 404)
        self.assertEqual(done.json()["sick_dates"], {str(LeaveRequest.objects.get().pk): ["2026-10-19"]})

    def test_the_date_of_birth_and_typical_day_can_be_set_over_the_api(self):
        self.as_user(self.hr)
        url = reverse("employee-pay-details", args=[self.worker.pk])
        done = self.client.put(url, {"date_of_birth": "2000-03-03", "typical_day_hours": "7.5"}).json()
        self.assertEqual((done["date_of_birth"], done["typical_day_hours"], done["has_date_of_birth"]), ("**/**/2000", "7.50", True))
        self.assertEqual(self.client.put(url, {"date_of_birth": "2999-01-01"}).status_code, 400)
        self.assertEqual(self.client.put(url, {"date_of_birth": "nope"}).status_code, 400)
