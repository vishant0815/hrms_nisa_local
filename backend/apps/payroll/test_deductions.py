"""Suggested deductions, the minimum wage check, bank holidays and the sick pay limit inside real pay runs."""
from datetime import date, timedelta
from decimal import Decimal as D

from django.test import override_settings
from rest_framework.exceptions import ValidationError

from apps.attendance.models import AttendanceRecord
from apps.leave.models import LeaveKind
from apps.payroll import calc, services
from apps.payroll.models import PayDetails, Payslip, PayslipTemplate
from apps.payroll.test_leave_pay import leave
from apps.payroll.test_payruns import MON, NEXT, PayBase, when


class DeductionBase(PayBase):
    def details(self, **data):
        return services.set_pay_details(self.hr, self.worker, data)

    def approve(self, run):
        for slip in run.payslips.all():
            services.set_deductions(self.hr, slip, {}, True)
        return services.approve_run(self.hr, run, PayslipTemplate.current())


class SuggestionTests(DeductionBase):
    def setUp(self):
        super().setUp()
        self.rec(0, "09:00", "17:00")
        self.rec(1, "09:00", "17:00")
        self.rec(3, "09:00", "17:00")
        self.rec(4, "09:00", "17:00")
        self.rec(5, "09:00", "16:00")  # 4 x 480 + 420 = 2340 minutes = 39h -> 468.00 at 12.00

    def test_new_starter_week_one_basis_gets_all_four_figures(self):
        self.details(tax_code="1257LW1", student_loan_plan="2", date_of_birth=date(1995, 1, 1))
        slip = self.slip(self.run_for())
        self.assertEqual(slip.gross_pay, D("468.00"))
        # NI (468 - 242) x 8% = 18.08 ; pension (468 - 120) x 5% = 17.40
        self.assertEqual(slip.pension, D("17.40"))
        # Pension comes off first (net pay arrangement): 468.00 - 17.40 = 450.60 ; less 241.92 free pay = 208.68 -> 208 x 20%
        self.assertEqual(slip.income_tax, D("41.60"))
        self.assertEqual(slip.national_insurance, D("18.08"))
        self.assertEqual(slip.student_loan, D("0"))
        self.assertFalse(slip.deductions_confirmed)

    def test_cumulative_code_uses_the_figures_from_the_p45(self):
        self.details(tax_code="1257L", date_of_birth=date(2007, 6, 1), prior_pay=D("13514"), prior_tax=D("1299.60"))
        slip = self.slip(self.run_for())
        self.assertEqual((slip.income_tax, slip.national_insurance, slip.pension), (D("45.20"), D("18.08"), D("0")))
        self.assertEqual(slip.deduction_notes, ["Not automatically enrolled because of age (22 to State Pension age); they can ask to join."])

    def test_a_cumulative_code_with_no_history_is_left_for_a_person_and_says_why(self):
        self.details(tax_code="1257L")
        slip = self.slip(self.run_for())
        self.assertEqual(slip.income_tax, D("0"))
        self.assertTrue(any(n.startswith("Income tax:") and "P45" in n for n in slip.deduction_notes))

    def test_what_a_person_typed_is_kept_when_the_system_cannot_work_it_out(self):
        self.details(tax_code="S1257L")
        run = self.run_for()
        services.set_deductions(self.hr, self.slip(run), {"income_tax": D("30.00")}, False)
        services.fill_run(run)
        slip = self.slip(run)
        self.assertEqual(slip.income_tax, D("30.00"))
        self.assertTrue(any("Scottish" in n for n in slip.deduction_notes))

    def test_a_confirmed_payslip_is_not_rewritten_but_a_changed_gross_asks_again(self):
        self.details(tax_code="1257LW1", date_of_birth=date(1995, 1, 1))
        run = self.run_for()
        services.set_deductions(self.hr, self.slip(run), {"income_tax": D("1.00")}, True)
        services.fill_run(run)
        self.assertEqual(self.slip(run).income_tax, D("1.00"))
        self.rec(2, "09:00", "10:00")
        services.fill_run(run)
        slip = self.slip(run)
        self.assertFalse(slip.deductions_confirmed)
        self.assertNotEqual(slip.income_tax, D("1.00"))

    def test_changing_the_pay_details_and_asking_again_updates_the_suggestion(self):
        self.details(tax_code="1257LW1", date_of_birth=date(1995, 1, 1))
        run = self.run_for()
        before = self.slip(run).student_loan
        self.details(student_loan_plan="1")
        slip = services.suggest_deductions(self.hr, self.slip(run))
        self.assertEqual((before, slip.student_loan), (D("0"), D("0")))  # 468 is under the Plan 1 threshold of 517
        self.details(pension_opted_out=True)
        slip = services.suggest_deductions(self.hr, self.slip(run))
        self.assertEqual(slip.pension, D("0"))
        self.assertTrue(any("Opted out" in n for n in slip.deduction_notes))

    def test_no_date_of_birth_means_no_ni_letter_and_a_person_must_fill_it_in(self):
        PayDetails.objects.filter(employee=self.worker).update(date_of_birth_encrypted="")
        self.details(tax_code="1257LW1")
        slip = self.slip(self.run_for())
        self.assertEqual(slip.national_insurance, D("0"))
        self.assertTrue(any("date of birth" in n for n in slip.deduction_notes))

    def test_someone_who_may_be_past_state_pension_age_needs_their_letter_set(self):
        self.details(tax_code="1257LW1", date_of_birth=date(1955, 5, 5))
        slip = self.slip(self.run_for())
        self.assertEqual(slip.national_insurance, D("0"))
        self.assertTrue(any(n.startswith("National Insurance:") and "State Pension age" in n for n in slip.deduction_notes))
        self.details(ni_category="C")
        self.assertFalse(any(n.startswith("National Insurance:") for n in services.suggest_deductions(self.hr, self.slip()).deduction_notes))

    def test_a_refund_replaces_a_typed_figure_with_nil_and_says_so(self):
        self.details(tax_code="1257L", prior_pay=D("0"), prior_tax=D("5000"))
        run = self.run_for()
        services.set_deductions(self.hr, self.slip(run), {"income_tax": D("30.00")}, False)
        services.fill_run(run)
        slip = self.slip(run)
        self.assertEqual(slip.income_tax, D("0"))
        self.assertTrue(any("due back" in n for n in slip.deduction_notes))

    def test_an_ni_letter_set_by_hand_is_used_and_checked(self):
        self.details(tax_code="1257LW1", ni_category="c")
        self.assertEqual(PayDetails.objects.get(employee=self.worker).ni_category, "C")
        self.assertEqual(self.slip(self.run_for()).national_insurance, D("0"))
        with self.assertRaises(ValidationError):
            self.details(ni_category="B")

    def test_prior_figures_come_as_a_pair_and_not_negative(self):
        for bad in ({"prior_pay": D("100")}, {"prior_pay": D("-1"), "prior_tax": D("0")}):
            with self.assertRaises(ValidationError):
                self.details(**bad)
        self.details(prior_pay=D("100"), prior_tax=D("10"))
        self.details(prior_pay=None, prior_tax=None)
        self.assertIsNone(PayDetails.objects.get(employee=self.worker).prior_pay)

    def test_tax_carries_on_from_the_runs_already_approved(self):
        self.details(tax_code="1257L", date_of_birth=date(2007, 6, 1), prior_pay=D("13514"), prior_tax=D("1299.60"))
        first = self.approve(self.run_for())
        self.assertEqual(self.slip(first).income_tax, D("45.20"))
        self.rec(0, "09:00", "17:00")  # not used: reuse the same weekdays next week
        AttendanceRecord.objects.all().delete()
        d = NEXT
        for n in range(4):
            AttendanceRecord.objects.create(
                employee=self.worker, store=self.store_a, clock_in_at=when(d + timedelta(days=n), "09:00"),
                clock_out_at=when(d + timedelta(days=n), "17:00"),
            )
        # Week 31. Pay so far = 13514 + 468 + 384 = 14366 ; free pay 31 x 241.92 = 7499.52 ; 6866.48 -> 6866 x 20% = 1373.20.
        # Tax already taken = 1299.60 + 45.20 = 1344.80 ; due now = 28.40.
        slip = self.slip(self.run_for(NEXT))
        self.assertEqual((slip.gross_pay, slip.income_tax), (D("384.00"), D("28.40")))

    def test_an_unlisted_tax_year_stops_the_run_with_a_clear_reason(self):
        from apps.payroll.models import PayRun

        run = self.run_for()
        PayRun.objects.filter(pk=run.pk).update(pay_date=date(2027, 4, 8))
        run.refresh_from_db()
        problems = services.blockers(run)
        self.assertTrue(any("tax figures" in p for p in problems), problems)


class MinimumWageTests(DeductionBase):
    def setUp(self):
        super().setUp()
        self.rec(0, "09:00", "17:00")  # Mon 19 Oct at 12.00

    def test_an_adult_on_12_pound_cannot_be_approved(self):
        self.details(date_of_birth=date(2000, 1, 1))
        run = self.run_for()
        self.assertTrue(any("below the minimum wage of £12.71" in p for p in services.blockers(run)))

    def test_the_same_rate_is_fine_for_a_19_year_old(self):
        self.assertFalse(any("minimum wage" in p for p in services.blockers(self.run_for())))

    def test_a_missing_date_of_birth_blocks_approval(self):
        PayDetails.objects.filter(employee=self.worker).update(date_of_birth_encrypted="")
        self.assertTrue(any("no date of birth" in p for p in services.blockers(self.run_for())))

    def test_a_birthday_in_the_week_changes_the_rate_that_applies(self):
        self.details(date_of_birth=date(2005, 10, 22))  # 21 on Thursday 22 Oct: 12.00 was fine before, not from then on
        self.rec(3, "09:00", "17:00")  # Thursday 22 Oct
        problems = [p for p in services.blockers(self.run_for()) if "minimum wage" in p]
        self.assertEqual(len(problems), 1)
        self.assertIn("22 Oct 2026", problems[0])

    def test_a_rate_exactly_on_the_minimum_is_fine(self):
        services.add_rate(self.hr, self.worker, D("10.85"), date(2026, 10, 1))
        self.assertFalse(any("minimum wage" in p for p in services.blockers(self.run_for())))

    def test_holiday_pay_days_are_checked_too(self):
        AttendanceRecord.objects.all().delete()
        self.details(date_of_birth=date(2000, 1, 1), typical_day_hours=D("8"))
        leave(self.worker, MON, MON)
        problems = services.blockers(self.run_for())
        self.assertTrue(any("below the minimum wage" in p for p in problems), problems)

    def test_the_apprentice_rate_applies_in_the_first_year(self):
        self.details(date_of_birth=date(2000, 1, 1), apprentice_from=date(2026, 9, 1))
        self.assertFalse(any("minimum wage" in p for p in services.blockers(self.run_for())))

    def test_entering_a_rate_below_the_minimum_is_refused_when_the_birthday_is_known(self):
        with self.assertRaises(ValidationError):
            services.add_rate(self.hr, self.worker, D("10.84"), date(2026, 10, 1))
        services.add_rate(self.hr, self.worker, D("10.85"), date(2026, 10, 1))

    def test_a_rate_can_be_entered_when_the_birthday_is_not_known_yet_but_approval_will_catch_it(self):
        PayDetails.objects.filter(employee=self.emp_b).delete()
        services.add_rate(self.hr, self.emp_b, D("5.00"), MON)
        self.rec(1, "09:00", "17:00", who=self.emp_b)
        problems = services.blockers(self.run_for())
        self.assertTrue(any("EMP-0002" in p and "no date of birth" in p for p in problems), problems)

    def test_a_rate_for_a_year_with_no_figures_yet_is_accepted_and_checked_later(self):
        services.add_rate(self.hr, self.worker, D("13.00"), date(2027, 4, 6))


class BankHolidayTests(DeductionBase):
    def test_england_bank_holidays_are_known(self):
        self.assertEqual(calc.bank_holiday(date(2026, 12, 25)), "Christmas Day")
        self.assertEqual(calc.bank_holiday(date(2026, 8, 31)), "Late Summer Bank Holiday")
        self.assertEqual(calc.bank_holiday(date(2026, 10, 26)), "")

    def work_on(self, day):
        AttendanceRecord.objects.create(
            employee=self.worker, store=self.store_a, clock_in_at=when(day, "09:00"), clock_out_at=when(day, "17:00"),
        )

    def test_a_bank_holiday_is_flagged_and_paid_normally_by_default(self):
        self.work_on(date(2026, 8, 31))
        slip = self.slip(self.run_for(date(2026, 8, 31)))
        self.assertEqual(slip.days[0]["bank_holiday"], "Late Summer Bank Holiday")
        self.assertEqual(slip.earnings, [{"label": "Basic pay", "minutes": 480, "rate": "12.00", "amount": "96.00"}])

    @override_settings(PAYROLL_BANK_HOLIDAY_MULTIPLIER="1.5")
    def test_a_premium_can_be_switched_on(self):
        self.work_on(date(2026, 8, 31))
        self.work_on(date(2026, 9, 1))
        slip = self.slip(self.run_for(date(2026, 8, 31)))
        self.assertEqual(
            [(e["label"], e["rate"], e["minutes"], e["amount"]) for e in slip.earnings],
            [("Basic pay", "12.00", 480, "96.00"), ("Bank holiday pay", "18.00", 480, "144.00")],
        )
        self.assertEqual(slip.gross_pay, D("240.00"))
        self.assertEqual(slip.paid_minutes, 960)

    def test_an_ordinary_day_is_not_flagged(self):
        self.work_on(MON)
        self.assertEqual(self.slip(self.run_for()).days[0]["bank_holiday"], "")


class SickPayLimitTests(DeductionBase):
    def test_sick_pay_cannot_exceed_the_weekly_rate_for_the_weeks_involved(self):
        leave(self.worker, MON + timedelta(days=1), MON + timedelta(days=2), kind=LeaveKind.SICK)
        slip = self.slip(self.run_for())
        with self.assertRaises(ValidationError):
            services.set_sick_pay(self.hr, slip, D("123.26"))
        services.set_sick_pay(self.hr, slip, D("123.25"))

    def test_the_weeks_are_counted_from_the_first_to_the_last_sick_day(self):
        class Slip:
            def __init__(self, *days):
                self.sick_dates = {"1": [d.isoformat() for d in days]}

        first = date(2026, 10, 19)
        for count, weeks in ((1, 1), (7, 1), (8, 2), (14, 2), (15, 3)):
            self.assertEqual(services._sick_weeks(Slip(first, first + timedelta(days=count - 1))), weeks, count)
        self.assertEqual(services._sick_weeks(Slip(first, first + timedelta(days=20))), 3)
