from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.attendance.models import AttendanceRecord, ReviewStatus
from apps.fingerprint import services as fingerprint
from apps.fingerprint.models import BreakPeriod
from apps.rota.models import Shift
from apps.rota.tests import RotaTestBase

from . import calc, services
from .models import HoursApproval, PayRun, Payslip, PayslipTemplate, RunStatus

MON = date(2026, 10, 19)
NEXT = MON + timedelta(days=7)


def when(d, clock):
    return timezone.make_aware(datetime.combine(d, time.fromisoformat(clock)))


class PayBase(RotaTestBase):
    def setUp(self):
        super().setUp()
        self.worker = self.emp_a
        services.add_rate(self.hr, self.worker, Decimal("12.00"), date(2026, 1, 1))
        # Everyone is 19 unless a test says otherwise: the minimum wage check needs a date of birth.
        services.set_pay_details(self.hr, self.worker, {"date_of_birth": date(2007, 6, 1)})

    def rec(self, n, start, end, who=None, shift=True, rota=None, **extra):
        """One finished record on day n of the pay week (clock times as 'HH:MM'; end may be next day)."""
        who = who or self.worker
        d = MON + timedelta(days=n)
        end_day = d + timedelta(days=1) if end <= start else d
        row = None
        if shift:
            a, b = rota or (start, end)
            row = Shift.objects.create(rota_week=self.week_a, employee=who, date=d, start_time=a, end_time=b)
        return AttendanceRecord.objects.create(
            employee=who, store=self.store_a, shift=row, clock_in_at=when(d, start), clock_out_at=when(end_day, end), **extra
        )

    def sample_week(self):
        fingerprint.register_device(store=self.store_a, name="Door", serial="Z-1")
        for n, a, b in [(0, "09:00", "17:00"), (1, "09:00", "17:00"), (3, "14:00", "20:00"), (4, "09:00", "17:00"), (5, "08:00", "17:00")]:
            record = self.rec(n, a, b)
            if n == 1:
                BreakPeriod.objects.create(record=record, started_at=when(MON + timedelta(days=1), "12:00"), ended_at=when(MON + timedelta(days=1), "12:40"))

    def run_for(self, start=MON, **kw):
        return services.create_pay_run(self.hr, start, **kw)

    def slip(self, run=None, who=None):
        return (run or PayRun.objects.get()).payslips.get(employee=who or self.worker)


class TaxWeekTests(SimpleTestCase):
    def test_week_one_starts_on_6_april(self):
        self.assertEqual(calc.tax_week(date(2026, 4, 6)), 1)
        self.assertEqual(calc.tax_week(date(2026, 4, 12)), 1)
        self.assertEqual(calc.tax_week(date(2026, 4, 13)), 2)
        self.assertEqual(calc.tax_week(date(2026, 10, 29)), 30)
        self.assertEqual(calc.tax_week(date(2026, 4, 5)), 53)  # a 365-day year ends in a 53rd part-week
        self.assertEqual(calc.tax_week(date(2027, 1, 1)), 39)


class PoundsTests(SimpleTestCase):
    def test_pay_is_rounded_to_the_penny_half_up(self):
        self.assertEqual(calc.pounds(Decimal(1) * Decimal("12.30") / 60), Decimal("0.21"))  # 0.205 exactly
        self.assertEqual(calc.pounds(Decimal("0.204")), Decimal("0.20"))
        self.assertEqual(calc.pounds(Decimal("466")), Decimal("466.00"))


class HoursToPayTests(PayBase):
    def test_the_sample_week_is_worked_out_from_the_clock_records(self):
        self.sample_week()
        slip = self.slip(self.run_for())
        self.assertEqual(slip.paid_minutes, 2330)
        self.assertEqual(slip.unpaid_break_minutes, 10)
        self.assertEqual(slip.gross_pay, Decimal("466.00"))
        self.assertEqual(slip.earnings, [{"label": "Basic pay", "minutes": 2330, "rate": "12.00", "amount": "466.00"}])
        tuesday = [d for d in slip.days if d["date"] == "2026-10-20"][0]
        self.assertEqual(
            (tuesday["rota"], tuesday["clocked"], tuesday["break_taken"], tuesday["unpaid_break"], tuesday["paid_minutes"]),
            ("09:00-17:00", "09:00-17:00", 40, 10, 470),
        )
        self.assertEqual(slip.store, self.store_a)
        self.assertEqual(slip.number, "2026-30-" + self.worker.employee_number)
        self.assertEqual(len(slip.days), 5)

    def test_break_time_is_only_taken_off_where_breaks_are_tracked(self):
        record = self.rec(0, "09:00", "17:00")
        BreakPeriod.objects.create(record=record, started_at=when(MON, "12:00"), ended_at=when(MON, "13:00"))
        self.assertEqual(self.slip(self.run_for()).paid_minutes, 450)  # a recorded break counts as tracked: 60 taken, 30 paid

    def test_no_break_records_and_no_device_means_nothing_is_taken_off(self):
        self.rec(0, "09:00", "17:00")
        slip = self.slip(self.run_for())
        self.assertEqual((slip.paid_minutes, slip.unpaid_break_minutes), (480, 0))

    def test_the_gross_pay_follows_the_rate_that_applied_on_each_day(self):
        services.add_rate(self.hr, self.worker, Decimal("13.00"), MON + timedelta(days=2))
        self.rec(0, "09:00", "17:00")
        self.rec(3, "09:00", "17:00")
        slip = self.slip(self.run_for())
        self.assertEqual(slip.gross_pay, Decimal("96.00") + Decimal("104.00"))
        self.assertEqual([(e["rate"], e["minutes"], e["amount"]) for e in slip.earnings],
                         [("12.00", 480, "96.00"), ("13.00", 480, "104.00")])

    def test_a_record_is_paid_in_the_week_it_started(self):
        self.rec(6, "23:30", "03:30")  # Sunday night into Monday: 4 hours, belongs to this week
        self.rec(7, "00:10", "04:10")  # Monday after: next week
        slip = self.slip(self.run_for())
        self.assertEqual(slip.paid_minutes, 240)

    def test_rejected_records_are_never_paid_or_listed(self):
        self.rec(0, "09:00", "17:00", review_status=ReviewStatus.REJECTED)
        self.rec(1, "09:00", "17:00")
        slip = self.slip(self.run_for())
        self.assertEqual((slip.paid_minutes, slip.held), (480, []))

    def test_unfinished_and_unreviewed_records_are_held_not_paid(self):
        pending = self.rec(0, "09:00", "17:00", review_status=ReviewStatus.PENDING)
        open_one = AttendanceRecord.objects.create(
            employee=self.worker, store=self.store_a, clock_in_at=when(MON + timedelta(days=1), "09:00")
        )
        self.rec(2, "09:00", "17:00")
        slip = self.slip(self.run_for())
        self.assertEqual(slip.paid_minutes, 480)
        self.assertEqual(
            sorted((h["record_id"], h["reason"]) for h in slip.held),
            sorted([(pending.pk, "needs_review"), (open_one.pk, "still_clocked_in")]),
        )

    def test_extra_hours_beyond_the_rota_wait_for_a_manager(self):
        short = self.rec(0, "09:00", "17:15", rota=("09:00", "17:00"))   # 15 minutes over: within tolerance
        long = self.rec(1, "09:00", "17:16", rota=("09:00", "17:00"))    # 16 minutes over: held
        slip = self.slip(self.run_for())
        self.assertEqual(slip.record_ids, [short.pk])
        self.assertEqual([(h["record_id"], h["reason"]) for h in slip.held], [(long.pk, "extra_hours")])

    def test_a_managers_ok_releases_extra_hours(self):
        long = self.rec(1, "09:00", "18:00", rota=("09:00", "17:00"))
        run = self.run_for()
        self.assertEqual(self.slip(run).paid_minutes, 0)
        services.approve_extra_hours(self.manager_a, long, "Stayed to do the delivery")
        services.fill_run(run)
        self.assertEqual(self.slip(run).paid_minutes, 540)
        self.assertEqual(HoursApproval.objects.get().approved_by, self.manager_a)

    def test_a_record_a_manager_already_approved_is_not_held_for_extra_hours(self):
        self.rec(1, "09:00", "20:00", rota=("09:00", "17:00"), review_status=ReviewStatus.APPROVED)
        self.assertEqual(self.slip(self.run_for()).paid_minutes, 660)

    def test_time_with_no_rota_shift_is_paid_as_entered(self):
        self.rec(1, "09:00", "13:00", shift=False)
        self.assertEqual(self.slip(self.run_for()).paid_minutes, 240)

    def test_hours_without_a_rate_are_held_and_stop_approval(self):
        other = self.emp_b
        record = AttendanceRecord.objects.create(
            employee=other, store=self.store_b, clock_in_at=when(MON, "09:00"), clock_out_at=when(MON, "17:00")
        )
        run = self.run_for()
        slip = self.slip(run, other)
        self.assertEqual((slip.paid_minutes, slip.gross_pay), (0, Decimal("0")))
        self.assertEqual([(h["record_id"], h["reason"]) for h in slip.held], [(record.pk, "no_rate")])
        self.assertTrue(any("no hourly rate" in p for p in services.blockers(run)))

    def test_a_rate_that_starts_after_the_work_does_not_count(self):
        services.add_rate(self.hr, self.emp_b, Decimal("11.00"), MON + timedelta(days=3))
        services.set_pay_details(self.hr, self.emp_b, {"date_of_birth": date(2007, 6, 1)})
        AttendanceRecord.objects.create(
            employee=self.emp_b, store=self.store_b, clock_in_at=when(MON, "09:00"), clock_out_at=when(MON, "17:00")
        )
        self.assertEqual(self.slip(self.run_for(), self.emp_b).held[0]["reason"], "no_rate")


class RollForwardTests(PayBase):
    def approve(self, run):
        for slip in run.payslips.all():
            services.set_deductions(self.hr, slip, {}, True)
        return services.approve_run(self.hr, run, PayslipTemplate.current())

    def test_held_hours_are_paid_in_a_later_run_and_only_once(self):
        pending = self.rec(0, "09:00", "17:00", review_status=ReviewStatus.PENDING)
        self.rec(1, "09:00", "17:00")
        first = self.approve(self.run_for())
        self.assertEqual(self.slip(first).paid_minutes, 480)
        AttendanceRecord.objects.filter(pk=pending.pk).update(review_status=ReviewStatus.APPROVED)
        second = self.approve(self.run_for(NEXT))
        self.assertEqual(self.slip(second).record_ids, [pending.pk])
        self.assertEqual(self.slip(second).gross_pay, Decimal("96.00"))
        third = self.run_for(NEXT + timedelta(days=7))
        self.assertEqual(third.payslips.count(), 0)

    def test_a_week_already_paid_is_not_paid_again_when_it_is_rebuilt_later(self):
        self.rec(1, "09:00", "17:00")
        first = self.approve(self.run_for())
        second = self.run_for(NEXT)
        self.assertEqual(second.payslips.count(), 0)
        self.assertEqual(self.slip(first).paid_minutes, 480)

    def test_attendance_from_before_the_first_run_is_treated_as_already_paid(self):
        self.rec(-3, "09:00", "17:00")  # the Friday before the first pay week
        self.rec(1, "09:00", "17:00")
        slip = self.slip(self.run_for())
        self.assertEqual(slip.paid_minutes, 480)

    def test_a_new_run_needs_the_earlier_draft_to_be_approved_first(self):
        self.run_for()
        with self.assertRaisesMessage(ValidationError, "still a draft"):
            self.run_for(NEXT)


class RunRulesTests(PayBase):
    def test_a_pay_week_starts_on_monday_and_is_made_once(self):
        with self.assertRaises(ValidationError):
            self.run_for(MON + timedelta(days=1))
        run = self.run_for()
        self.assertEqual((run.period_end, run.pay_date), (date(2026, 10, 25), date(2026, 10, 29)))
        self.assertEqual(run.pay_date.weekday(), 3)
        with self.assertRaises(ValidationError):
            self.run_for()

    def test_the_pay_date_must_come_after_the_week(self):
        with self.assertRaises(ValidationError):
            self.run_for(pay_date=date(2026, 10, 25))
        self.assertEqual(self.run_for(pay_date=date(2026, 10, 30)).pay_date, date(2026, 10, 30))


class DeductionTests(PayBase):
    def setUp(self):
        super().setUp()
        self.rec(0, "09:00", "17:00")
        self.run = self.run_for()
        self.slip_ = self.slip(self.run)

    def test_deductions_are_entered_and_net_pay_follows(self):
        slip = services.set_deductions(self.hr, self.slip_, {"income_tax": Decimal("10.00"), "national_insurance": Decimal("5.50"), "pension": Decimal("1.25")}, True)
        self.assertEqual(slip.total_deductions, Decimal("16.75"))
        self.assertEqual(slip.net_pay, Decimal("79.25"))
        self.assertTrue(slip.deductions_confirmed)
        self.assertEqual(slip.deductions_by, self.hr)

    def test_deductions_cannot_be_negative_or_more_than_the_gross_pay(self):
        with self.assertRaises(ValidationError):
            services.set_deductions(self.hr, self.slip_, {"income_tax": Decimal("-1")}, None)
        with self.assertRaises(ValidationError):
            services.set_deductions(self.hr, self.slip_, {"income_tax": Decimal("96.01")}, None)
        services.set_deductions(self.hr, self.slip_, {"income_tax": Decimal("96.00")}, None)

    def test_a_rebuild_keeps_the_figures_and_the_confirmation_when_the_pay_did_not_change(self):
        services.set_deductions(self.hr, self.slip_, {"income_tax": Decimal("9.00")}, True)
        services.fill_run(self.run)
        again = self.slip(self.run)
        self.assertEqual((again.income_tax, again.deductions_confirmed), (Decimal("9.00"), True))

    def test_a_rebuild_asks_for_the_tax_to_be_checked_again_when_the_pay_changed(self):
        services.set_deductions(self.hr, self.slip_, {"income_tax": Decimal("9.00")}, True)
        self.rec(1, "09:00", "13:00")
        services.fill_run(self.run)
        again = self.slip(self.run)
        self.assertEqual((again.gross_pay, again.income_tax, again.deductions_confirmed), (Decimal("144.00"), Decimal("9.00"), False))

    def test_a_person_with_nothing_left_to_pay_drops_off_the_draft(self):
        AttendanceRecord.objects.update(review_status=ReviewStatus.REJECTED)
        services.fill_run(self.run)
        self.assertEqual(self.run.payslips.count(), 0)


class ApprovalTests(PayBase):
    def setUp(self):
        super().setUp()
        self.rec(0, "09:00", "17:00")
        self.run = self.run_for()

    def test_approval_waits_until_the_deductions_are_confirmed(self):
        with self.assertRaises(ValidationError) as caught:
            services.approve_run(self.hr, self.run, PayslipTemplate.current())
        self.assertIn("have not been confirmed", str(caught.exception.detail["problems"][0]))
        self.assertEqual(PayRun.objects.get().status, RunStatus.DRAFT)

    def test_an_approved_run_is_frozen_with_the_template_it_was_approved_with(self):
        PayslipTemplate.current()
        services.update_template(self.hr, {"legal_employer_name": "Shop Ltd"})
        services.set_deductions(self.hr, self.slip(self.run), {}, True)
        run = services.approve_run(self.owner, self.run, PayslipTemplate.current())
        self.assertEqual((run.status, run.approved_by), (RunStatus.APPROVED, self.owner))
        self.assertIsNotNone(run.approved_at)
        services.update_template(self.hr, {"legal_employer_name": "Changed Ltd"})
        self.assertEqual(PayRun.objects.get().template_snapshot["legal_employer_name"], "Shop Ltd")
        for action in (
            lambda: services.fill_run(run),
            lambda: services.set_deductions(self.hr, self.slip(run), {"income_tax": Decimal("1")}, None),
            lambda: services.approve_run(self.hr, run, PayslipTemplate.current()),
        ):
            with self.assertRaises(ValidationError):
                action()

    def test_net_pay_below_zero_stops_approval(self):
        Payslip.objects.update(income_tax=Decimal("200"), deductions_confirmed=True)
        with self.assertRaises(ValidationError) as caught:
            services.approve_run(self.hr, self.run, PayslipTemplate.current())
        self.assertIn("below zero", caught.exception.detail["problems"][0])

    def test_hours_that_were_paid_in_another_run_stop_approval(self):
        services.set_deductions(self.hr, self.slip(self.run), {}, True)
        other = PayRun.objects.create(period_start=NEXT, period_end=NEXT + timedelta(days=6), pay_date=NEXT + timedelta(days=10), status=RunStatus.APPROVED)
        Payslip.objects.create(pay_run=other, employee=self.emp_b, number="x", record_ids=self.slip(self.run).record_ids)
        with self.assertRaisesMessage(ValidationError, "already paid"):
            services.approve_run(self.hr, self.run, PayslipTemplate.current())


class EdgeCaseTests(PayBase):
    def test_a_payslip_belongs_to_the_shop_where_most_of_the_paid_time_was_worked(self):
        self.rec(0, "09:00", "13:00")  # 4 hours at shop A
        AttendanceRecord.objects.create(
            employee=self.worker, store=self.store_b, clock_in_at=when(MON + timedelta(days=1), "09:00"),
            clock_out_at=when(MON + timedelta(days=1), "17:00"),
        )  # 8 hours at shop B
        self.assertEqual(self.slip(self.run_for()).store, self.store_b)

    def test_a_break_recorded_beyond_the_shift_cannot_make_paid_time_negative(self):
        record = self.rec(0, "09:00", "15:00")  # 6 hours
        BreakPeriod.objects.create(record=record, started_at=when(MON, "10:00"), ended_at=when(MON + timedelta(days=1), "10:00"))
        slip = self.slip(self.run_for())
        self.assertEqual((slip.paid_minutes, slip.unpaid_break_minutes), (0, 360))
