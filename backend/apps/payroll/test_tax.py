"""The calculators against figures worked out by hand from HMRC's PAYE specification (2026/27)."""
from datetime import date
from decimal import Decimal as D

from django.test import SimpleTestCase

from . import minwage, tax, taxrates

R = taxrates.RATES["2026/27"]
Z = D("0")


class FreePayTests(SimpleTestCase):
    def test_free_pay_follows_the_specification(self):
        per = R["tax"]["free_pay_per_500"]
        for code, expected in [(0, "0"), (1, "0.37"), (500, "96.33"), (501, "96.53"), (1000, "192.49"), (1257, "241.92")]:
            self.assertEqual(tax.code_value(code, per), D(expected), code)


class CodeTests(SimpleTestCase):
    def test_codes_are_understood(self):
        self.assertEqual(tax.parse_code("1257L"), ("std", 1257, True))
        self.assertEqual(tax.parse_code(" 1257l "), ("std", 1257, True))
        self.assertEqual(tax.parse_code("1257LW1"), ("std", 1257, False))
        self.assertEqual(tax.parse_code("1257LX"), ("std", 1257, False))
        self.assertEqual(tax.parse_code("K475"), ("k", 475, True))
        self.assertEqual(tax.parse_code("0T"), ("std", 0, True))
        self.assertEqual(tax.parse_code("BR"), ("BR", 0, True))
        self.assertEqual(tax.parse_code("BRW1"), ("BR", 0, False))
        self.assertEqual(tax.parse_code("NT"), ("NT", 0, True))

    def test_codes_that_are_not_supported_are_refused_not_guessed(self):
        for bad in ("", "S1257L", "C1257L", "SBR", "XYZ", "1257", "L1257"):
            with self.assertRaises(tax.Unsupported, msg=bad):
                tax.parse_code(bad)


class IncomeTaxTests(SimpleTestCase):
    def paye(self, code, week, now, before=0, paid=0):
        return tax.paye(code, week, D(str(now)), D(str(before)), D(str(paid)), R)

    def test_week_one_basic_rate(self):
        self.assertEqual(self.paye("1257L", 1, 466), D("44.80"))  # (466 - 241.92) = 224.08 -> 224 x 20%
        self.assertEqual(self.paye("1257L", 1, "241.92"), Z)
        self.assertEqual(self.paye("1257L", 1, "242.92"), D("0.20"))
        self.assertEqual(self.paye("1257L", 1, 300), D("11.60"))

    def test_the_higher_rate_starts_above_the_weekly_share_of_37700(self):
        # 37,700 / 52 = 725 a week. 1000 - 241.92 = 758.08 -> 758. 725 x 20% = 145, 33 x 40% = 13.20.
        self.assertEqual(self.paye("1257L", 1, 1000), D("158.20"))
        self.assertEqual(self.paye("1257L", 1, "966.92"), D("145.00"))  # exactly on the band edge: all basic rate
        self.assertEqual(self.paye("1257L", 1, "967.92"), D("145.40"))

    def test_the_additional_rate(self):
        # 125,140 / 52 = 2406.5385 -> test value 2407. Pay of 3000 less 241.92 = 2758.08 -> 2758.
        # k2 = 42,516 / 52 = 817.6154 ; 2758 - 2406.5385 = 351.4615 x 45% = 158.1576 -> total 975.7730 -> 975.77
        self.assertEqual(self.paye("1257L", 1, 3000), D("975.77"))

    def test_a_fractional_band_edge_is_rounded_up_before_the_test(self):
        # 125,140 / 52 = 2406.5385 -> test value 2407. Pay 2648.72 - 241.92 = 2406.80 is not above 2407, so the
        # higher band applies: 725 x 20% + (2406 - 725) x 40% = 817.40 (the additional band would give 817.37).
        self.assertEqual(self.paye("1257L", 1, "2648.72"), D("817.40"))

    def test_fixed_rate_codes(self):
        self.assertEqual(self.paye("BR", 1, "100.99"), D("20.00"))
        self.assertEqual(self.paye("D0", 1, "100.50"), D("40.00"))
        self.assertEqual(self.paye("D1", 1, 100), D("45.00"))
        self.assertEqual(self.paye("NT", 1, 5000), Z)
        self.assertEqual(self.paye("0T", 1, 100), D("20.00"))

    def test_a_k_code_adds_to_pay_and_tax_is_capped_at_half_the_pay(self):
        # K1000 adds 192.49: (100 + 192.49) = 292.49 -> 292 x 20% = 58.40, but only 50% of 100 may be taken.
        self.assertEqual(self.paye("K1000", 1, 100), D("50.00"))
        # 300 + 192.49 = 492.49 -> 492 x 20% = 98.40 (under half of 300)
        self.assertEqual(self.paye("K1000", 1, 300), D("98.40"))

    def test_cumulative_tax_builds_up_week_by_week(self):
        self.assertEqual(self.paye("1257L", 2, 466, 466, "44.80"), D("44.80"))  # 932 - 483.84 = 448.16 -> 448 x 20% = 89.60
        self.assertEqual(self.paye("1257L", 30, 466, 466 * 29, "1299.60"), D("44.80"))

    def test_a_quiet_week_gives_a_refund_that_is_not_limited(self):
        self.assertEqual(self.paye("1257L", 2, 0, 466, "44.80"), D("-44.80"))

    def test_catching_up_is_limited_to_half_the_pay(self):
        # 29 weeks of 466 untaxed, then 500: owed 1344.40 but only 250 can be taken now.
        self.assertEqual(self.paye("1257L", 30, 500, 466 * 29, 0), D("250.00"))

    def test_week_one_codes_ignore_what_came_before(self):
        self.assertEqual(self.paye("1257LW1", 20, 466, 9999, 999), D("44.80"))

    def test_no_pay_means_no_tax(self):
        self.assertEqual(self.paye("1257L", 1, 0), Z)


class NationalInsuranceTests(SimpleTestCase):
    def ni(self, letter, gross):
        return tax.employee_ni(letter, D(str(gross)), R)

    def test_the_thresholds(self):
        self.assertEqual(self.ni("A", "241.99"), Z)
        self.assertEqual(self.ni("A", 242), Z)
        self.assertEqual(self.ni("A", "242.01"), D("0.00"))
        self.assertEqual(self.ni("A", 466), D("17.92"))
        self.assertEqual(self.ni("A", 967), D("58.00"))
        self.assertEqual(self.ni("A", 1067), D("60.00"))

    def test_an_exact_half_penny_goes_down(self):
        self.assertEqual(self.ni("A", "967.25"), D("58.00"))

    def test_the_letters(self):
        self.assertEqual(self.ni("M", 466), D("17.92"))
        self.assertEqual(self.ni("H", 466), D("17.92"))
        self.assertEqual(self.ni("C", 466), Z)
        self.assertEqual(self.ni("J", 466), D("4.48"))
        self.assertEqual(self.ni("a", 466), D("17.92"))
        with self.assertRaises(tax.Unsupported):
            self.ni("B", 466)


class StudentLoanTests(SimpleTestCase):
    def test_each_plan_takes_its_share_above_its_threshold_in_whole_pounds(self):
        self.assertEqual(tax.student_loan_threshold("2", R), (D("565"), D("0.09")))
        self.assertEqual(tax.student_loan(["2"], D("700"), R), D("12"))   # 135 x 9% = 12.15
        self.assertEqual(tax.student_loan(["2"], D("565.99"), R), Z)
        self.assertEqual(tax.student_loan(["1"], D("700"), R), D("16"))   # 183 x 9% = 16.47
        self.assertEqual(tax.student_loan(["postgrad"], D("700"), R), D("17"))  # 297 x 6% = 17.82
        self.assertEqual(tax.student_loan(["2", "postgrad"], D("700"), R), D("29"))
        self.assertEqual(tax.student_loan([], D("700"), R), Z)
        self.assertEqual(tax.student_loan(["5"], D("481"), R), Z)  # threshold 480: 1 x 9% = 0.09 -> 0


class PensionTests(SimpleTestCase):
    def pension(self, gross, age, out=False):
        return tax.pension(D(str(gross)), age, out, R)[0]

    def test_qualifying_earnings_band(self):
        self.assertEqual(self.pension(466, 30), D("17.30"))      # (466 - 120) x 5%
        self.assertEqual(self.pension(1500, 30), D("42.35"))     # capped at 967: 847 x 5%
        self.assertEqual(self.pension("192.01", 30), D("3.60"))
        self.assertEqual(self.pension(192, 30), Z)

    def test_who_is_enrolled(self):
        self.assertEqual(self.pension(466, 30, out=True), Z)
        self.assertEqual(self.pension(466, 21), Z)
        self.assertGreater(self.pension(466, 22), 0)
        self.assertGreater(self.pension(466, 65), 0)
        self.assertEqual(self.pension(466, 66), Z)
        with self.assertRaises(tax.Unsupported):
            tax.pension(D("466"), None, False, R)


class YearTests(SimpleTestCase):
    def test_a_year_without_figures_is_refused(self):
        self.assertEqual(taxrates.rates_for(date(2026, 4, 6)), R)
        self.assertEqual(taxrates.rates_for(date(2027, 4, 5)), R)
        for day in (date(2026, 4, 5), date(2027, 4, 6)):
            with self.assertRaises(taxrates.UnsupportedTaxYear):
                taxrates.rates_for(day)


class MinimumWageTests(SimpleTestCase):
    def rate(self, born, day, apprentice=None, rates=R):
        return minwage.minimum_rate(born, apprentice, day, rates)

    def test_the_rate_changes_on_the_birthday(self):
        born = date(2005, 10, 22)  # 21 on 22 Oct 2026
        self.assertEqual(self.rate(born, date(2026, 10, 21)), D("10.85"))
        self.assertEqual(self.rate(born, date(2026, 10, 22)), D("12.71"))
        young = date(2008, 10, 22)  # 18 on 22 Oct 2026
        self.assertEqual(self.rate(young, date(2026, 10, 21)), D("8.00"))
        self.assertEqual(self.rate(young, date(2026, 10, 22)), D("10.85"))

    def test_apprentices_have_their_own_rate_for_the_first_year_or_while_under_19(self):
        born = date(2004, 1, 1)  # 22
        start = date(2026, 9, 1)
        self.assertEqual(self.rate(born, date(2026, 10, 1), start), D("8.00"))
        self.assertEqual(self.rate(born, date(2027, 8, 31), start), D("8.00"))
        self.assertEqual(self.rate(born, date(2027, 9, 1), start), D("12.71"))
        self.assertEqual(self.rate(born, date(2026, 8, 31), start), D("12.71"))  # before the apprenticeship began
        self.assertEqual(self.rate(date(2008, 3, 3), date(2026, 10, 1), start), D("8.00"))  # 18, apprentice
        self.assertEqual(self.rate(date(2006, 3, 3), date(2026, 10, 1), date(2025, 9, 1)), D("10.85"))  # 20, past the first year: age rate

    def test_an_apprentice_under_19_keeps_the_apprentice_rate_after_the_first_year(self):
        self.assertEqual(self.rate(date(2008, 3, 3), date(2026, 10, 1), date(2025, 9, 1)), D("8.00"))

    def test_without_a_birthday_nothing_can_be_said(self):
        self.assertIsNone(self.rate(None, date(2026, 10, 1)))
