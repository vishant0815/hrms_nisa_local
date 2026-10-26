import csv
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO, StringIO

from django.urls import reverse
from pypdf import PdfReader
from rest_framework.exceptions import ValidationError

from apps.payroll import calc, services
from apps.payroll.models import PayDetails, PayRun, Payslip, PayslipTemplate, RunStatus
from apps.payroll.test_payroll_api import ApiBase
from apps.payroll.test_payruns import MON, NEXT, when
from apps.payroll.views import spreadsheet_safe
from apps.attendance.models import AttendanceRecord
from apps.payroll.tests import picture


def pdf_text(response):
    reader = PdfReader(BytesIO(b"".join(response.streaming_content)))
    assert len(reader.pages) == 1, "a payslip must fit on one page"
    return reader.pages[0].extract_text()


class DocBase(ApiBase):
    def setUp(self):
        super().setUp()
        self.sample_week()
        services.update_template(self.hr, {
            "legal_employer_name": "Sample Retail Ltd", "paye_reference": "123/AB456",
            "hr_email": "hr@sample.example", "trading_name": "Nisa Local",
        })
        services.set_pay_details(self.hr, self.worker, {
            "ni_number": "AB123456C", "tax_code": "1257L", "sort_code": "12-34-56", "account_number": "12345678",
            "account_name": "S Employee",
        })
        self.run = self.run_for()

    def approve(self, tax="44.80"):
        for slip in self.run.payslips.all():
            services.set_deductions(self.hr, slip, {"income_tax": Decimal(tax), "national_insurance": Decimal("17.92"), "pension": Decimal("17.30")}, True)
        services.approve_run(self.hr, self.run, PayslipTemplate.current())
        self.run.refresh_from_db()


class TaxYearTests(DocBase):
    def test_labels(self):
        self.assertEqual(calc.tax_year_label(MON), "2026/27")
        self.assertEqual(calc.tax_year_label(MON.replace(month=3, day=1)), "2025/26")


class PdfTests(DocBase):
    def test_the_payslip_pdf_carries_the_numbers_and_the_template_wording(self):
        services.set_logo(self.hr, picture("PNG"))
        self.approve()
        self.as_user(self.hr)
        text = pdf_text(self.client.get(reverse("payslip-pdf", args=[self.slip(self.run).pk])))
        for expected in [
            "Nisa Local", "Sample Retail Ltd", "PAYE ref: 123/AB456", "AB******C", "1257L", "19 Oct - 25 Oct 2026",
            "Thu 29 Oct 2026", "30 (2026/27)", "£466.00", "£44.80", "£17.92", "£17.30", "£80.02", "£385.98",
            "38.83", "ending 5678", "**-**-56", "WAGES 29OCT", "hr@sample.example", "Shop A", "40 min", "10 min",
        ]:
            self.assertIn(expected, text)
        self.assertNotIn("12345678", text)
        self.assertNotIn("123456", text.replace("123456", "", 0).replace("AB123456C", ""))
        self.assertNotIn("Holiday pay", text)

    def test_the_pdf_is_made_without_a_logo_and_hides_the_bank_line_when_asked(self):
        services.update_template(self.hr, {"show_bank_line": False})
        self.approve()
        self.as_user(self.hr)
        text = pdf_text(self.client.get(reverse("payslip-pdf", args=[self.slip(self.run).pk])))
        self.assertNotIn("account ending", text)
        self.assertIn("£385.98", text)

    def test_the_pdf_shows_the_template_as_it_was_when_approved(self):
        self.approve()
        services.update_template(self.hr, {"legal_employer_name": "Changed Ltd"})
        self.as_user(self.hr)
        text = pdf_text(self.client.get(reverse("payslip-pdf", args=[self.slip(self.run).pk])))
        self.assertIn("Sample Retail Ltd", text)
        self.assertNotIn("Changed Ltd", text)

    def test_year_to_date_adds_up_the_runs_in_the_tax_year(self):
        self.approve()
        self.rec(7, "09:00", "17:00")
        second = self.run_for(NEXT)
        for slip in second.payslips.all():
            services.set_deductions(self.hr, slip, {"income_tax": Decimal("10.00")}, True)
        services.approve_run(self.hr, second, PayslipTemplate.current())
        total = services.year_to_date(self.slip(second))
        self.assertEqual((total["gross_pay"], total["income_tax"], total["tax_week"]), (Decimal("562.00"), Decimal("54.80"), 31))
        self.assertEqual(total["net_pay"], total["gross_pay"] - total["total_deductions"])
        self.assertEqual(services.year_to_date(self.slip(self.run))["gross_pay"], Decimal("466.00"))

    def test_the_hours_table_is_in_date_order_and_its_total_row_is_right(self):
        self.approve()
        self.as_user(self.hr)
        text = pdf_text(self.client.get(reverse("payslip-pdf", args=[self.slip(self.run).pk])))
        self.assertLess(text.index("Mon 19 Oct"), text.index("Tue 20 Oct"))
        self.assertLess(text.index("Tue 20 Oct"), text.index("Sat 24 Oct"))
        lines = text.splitlines()
        window = lines[lines.index("Total"):][:4]
        self.assertIn("10 min", window)
        self.assertIn("38.83", window)

    def test_year_to_date_ignores_pay_from_an_earlier_tax_year(self):
        self.approve()
        old = PayRun.objects.create(
            period_start=date(2026, 3, 23), period_end=date(2026, 3, 29), pay_date=date(2026, 4, 2), status=RunStatus.SENT
        )
        Payslip.objects.create(pay_run=old, employee=self.worker, number="old", gross_pay=Decimal("1000"), income_tax=Decimal("100"))
        total = services.year_to_date(self.slip(self.run))
        self.assertEqual((total["gross_pay"], total["income_tax"]), (Decimal("466.00"), Decimal("44.80")))

    def test_the_pdf_is_not_there_before_approval(self):
        self.as_user(self.hr)
        self.assertEqual(self.client.get(reverse("payslip-pdf", args=[self.slip(self.run).pk])).status_code, 404)

    def test_an_employee_gets_their_own_pdf_and_nobody_elses(self):
        self.approve()
        AttendanceRecord.objects.create(employee=self.emp_b, store=self.store_b, clock_in_at=when(MON, "09:00"), clock_out_at=when(MON, "17:00"))
        mine = self.slip(self.run)
        self.as_user(self.worker.user)
        response = self.client.get(reverse("my-payslip-pdf", args=[mine.pk]))
        self.assertEqual((response.status_code, response["Content-Type"]), (200, "application/pdf"))
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(self.client.get(reverse("payslip-pdf", args=[mine.pk])).status_code, 403)
        self.as_user(self.emp_b.user)
        self.assertEqual(self.client.get(reverse("my-payslip-pdf", args=[mine.pk])).status_code, 404)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(reverse("my-payslip-pdf", args=[mine.pk])).status_code, 401)


class BankFileTests(DocBase):
    def fetch(self, user=None):
        self.as_user(user or self.hr)
        return self.client.get(reverse("pay-run-bank-file", args=[self.run.pk]))

    def test_the_bank_file_lists_each_net_payment(self):
        self.approve()
        response = self.fetch()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("bank-payments-2026-10-29.csv", response["Content-Disposition"])
        self.assertIn("no-store", response["Cache-Control"])
        rows = list(csv.reader(StringIO(response.content.decode())))
        self.assertEqual(rows, [
            ["Name", "Sort code", "Account number", "Amount", "Reference"],
            ["S Employee", "12-34-56", "12345678", "385.98", "WAGES 29OCT"],
        ])

    def test_half_a_set_of_bank_details_is_not_enough(self):
        services.set_pay_details(self.hr, self.worker, {"account_number": ""})
        self.approve()
        self.assertEqual(self.fetch().status_code, 400)
        services.set_pay_details(self.hr, self.worker, {"account_number": "12345678", "sort_code": ""})
        self.assertEqual(self.fetch().status_code, 400)

    def test_it_is_not_made_for_a_draft(self):
        self.assertEqual(self.fetch().status_code, 400)

    def test_people_without_bank_details_are_named_and_no_file_is_made(self):
        AttendanceRecord.objects.create(employee=self.emp_b, store=self.store_b, clock_in_at=when(MON, "09:00"), clock_out_at=when(MON, "17:00"))
        services.add_rate(self.hr, self.emp_b, Decimal("11.00"), MON)
        services.fill_run(self.run)
        self.approve()
        response = self.fetch()
        self.assertEqual(response.status_code, 400)
        self.assertIn(self.emp_b.employee_number, response.json()["bank_details"])
        self.assertNotIn(self.worker.employee_number, response.json()["bank_details"])

    def test_someone_paid_nothing_net_is_left_out(self):
        self.approve()
        Payslip.objects.update(income_tax=Decimal("466.00"), national_insurance=0, pension=0)
        rows = list(csv.reader(StringIO(self.fetch().content.decode())))
        self.assertEqual(len(rows), 1)

    def test_only_owner_and_hr_get_it(self):
        self.approve()
        for user in (self.manager_a, self.worker.user):
            self.assertEqual(self.fetch(user).status_code, 403)
        self.assertEqual(self.fetch(self.owner).status_code, 200)

    def test_a_name_that_could_run_as_a_spreadsheet_formula_is_defused(self):
        for bad in ("=HYPERLINK(1)", "+1", "-1", "@x", "\tx"):
            self.assertTrue(spreadsheet_safe(bad).startswith("'"), bad)
        self.assertEqual(spreadsheet_safe("Ann Lee"), "Ann Lee")
        services.set_pay_details(self.hr, self.worker, {"account_name": "=CMD()"})
        self.approve()
        self.assertEqual(list(csv.reader(StringIO(self.fetch().content.decode())))[1][0], "'=CMD()")

    def test_long_names_are_cut_to_the_18_characters_banks_accept(self):
        services.set_pay_details(self.hr, self.worker, {"account_name": "A Very Long Account Holder Name"})
        self.approve()
        self.assertEqual(list(csv.reader(StringIO(self.fetch().content.decode())))[1][0], "A Very Long Accoun")
