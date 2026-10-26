from datetime import date, timedelta
from decimal import Decimal

from django.db import connection
from django.urls import reverse
from rest_framework.exceptions import ValidationError

from apps.attendance.models import AttendanceRecord
from apps.payroll import crypto, services
from apps.payroll.models import HoursApproval, PayDetails, PayRate, PayRun, Payslip, RunStatus
from apps.payroll.test_payruns import MON, NEXT, PayBase, when


class ApiBase(PayBase):
    def as_user(self, user):
        self.client.force_authenticate(user)

    def make_run_over_http(self):
        self.as_user(self.hr)
        response = self.client.post(reverse("pay-runs"), {"period_start": MON.isoformat()})
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()


class PermissionTests(ApiBase):
    def setUp(self):
        super().setUp()
        self.rec(0, "09:00", "17:00")
        self.run = self.run_for()
        self.slip_ = self.slip(self.run)
        self.targets = [
            ("get", reverse("pay-runs"), None),
            ("post", reverse("pay-runs"), {"period_start": NEXT.isoformat()}),
            ("get", reverse("pay-run", args=[self.run.pk]), None),
            ("post", reverse("pay-run-rebuild", args=[self.run.pk]), None),
            ("post", reverse("pay-run-approve", args=[self.run.pk]), None),
            ("get", reverse("payslip", args=[self.slip_.pk]), None),
            ("patch", reverse("payslip-deductions", args=[self.slip_.pk]), {"income_tax": "1"}),
            ("get", reverse("employee-pay-rates", args=[self.worker.pk]), None),
            ("post", reverse("employee-pay-rates", args=[self.worker.pk]), {"hourly_rate": "20", "effective_from": "2026-12-01"}),
            ("get", reverse("employee-pay-details", args=[self.worker.pk]), None),
            ("put", reverse("employee-pay-details", args=[self.worker.pk]), {"tax_code": "1257L"}),
        ]

    def test_only_owner_and_hr_can_use_payroll_pages(self):
        for user in (self.manager_a, self.worker.user, self.emp_b.user):
            self.as_user(user)
            for method, url, body in self.targets:
                self.assertEqual(getattr(self.client, method)(url, body).status_code, 403, (user.role, method, url))
        self.client.force_authenticate(None)
        for method, url, body in self.targets:
            self.assertEqual(getattr(self.client, method)(url, body).status_code, 401, url)
        self.assertEqual(PayRate.objects.count(), 1)
        self.assertEqual(PayRun.objects.count(), 1)

    def test_owner_and_hr_can(self):
        for user in (self.owner, self.hr):
            self.as_user(user)
            self.assertEqual(self.client.get(reverse("pay-runs")).status_code, 200)
            self.assertEqual(self.client.get(reverse("payslip", args=[self.slip_.pk])).status_code, 200)


class RunOverHttpTests(ApiBase):
    def test_a_run_is_created_checked_confirmed_and_approved_through_the_api(self):
        self.rec(0, "09:00", "17:00")
        data = self.make_run_over_http()
        self.assertEqual((data["status"], data["pay_date"], data["total_gross"]), ("draft", "2026-10-29", "96.00"))
        self.assertEqual(len(data["problems"]), 1)
        slip_id = data["payslips"][0]["id"]
        approve = reverse("pay-run-approve", args=[data["id"]])
        refused = self.client.post(approve)
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(len(refused.json()["problems"]), 1)
        patched = self.client.patch(
            reverse("payslip-deductions", args=[slip_id]),
            {"income_tax": "8.00", "national_insurance": "3.00", "confirmed": True},
        ).json()
        self.assertEqual((patched["net_pay"], patched["total_deductions"]), ("85.00", "11.00"))
        done = self.client.post(approve)
        self.assertEqual((done.status_code, done.json()["status"], done.json()["problems"]), (200, "approved", []))

    def test_bad_input_is_a_clear_400_not_a_crash(self):
        self.as_user(self.hr)
        url = reverse("pay-runs")
        self.assertEqual(self.client.post(url, {}).status_code, 400)
        self.assertEqual(self.client.post(url, {"period_start": "nonsense"}).status_code, 400)
        self.assertEqual(self.client.post(url, {"period_start": (MON + timedelta(days=1)).isoformat()}).status_code, 400)
        self.assertEqual(self.client.get(reverse("pay-run", args=[999])).status_code, 404)
        self.assertEqual(self.client.patch(reverse("payslip-deductions", args=[999]), {}).status_code, 404)


class RatesTests(ApiBase):
    def test_rates_are_added_and_listed_newest_first(self):
        self.as_user(self.hr)
        url = reverse("employee-pay-rates", args=[self.worker.pk])
        self.assertEqual(self.client.post(url, {"hourly_rate": "12.50", "effective_from": "2026-04-01"}).status_code, 201)
        rows = self.client.get(url).json()
        self.assertEqual([r["effective_from"] for r in rows], ["2026-04-01", "2026-01-01"])
        self.assertEqual(rows[0]["hourly_rate"], "12.50")
        self.assertEqual(PayRate.objects.get(effective_from="2026-04-01").set_by, self.hr)

    def test_a_zero_negative_or_repeated_rate_is_refused(self):
        self.as_user(self.hr)
        url = reverse("employee-pay-rates", args=[self.worker.pk])
        for bad in ("0", "-5", "abc", "1234567"):
            self.assertEqual(self.client.post(url, {"hourly_rate": bad, "effective_from": "2026-05-01"}).status_code, 400, bad)
        again = self.client.post(url, {"hourly_rate": "13", "effective_from": "2026-01-01"})
        self.assertEqual(again.status_code, 400)
        self.assertIn("effective_from", again.json())
        self.assertEqual(PayRate.objects.count(), 1)
        with self.assertRaises(ValidationError) as caught:
            services.add_rate(self.hr, self.worker, Decimal("0"), MON)
        self.assertIn("hourly_rate", caught.exception.detail)


class PayDetailsTests(ApiBase):
    def put(self, body):
        self.as_user(self.hr)
        return self.client.put(reverse("employee-pay-details", args=[self.worker.pk]), body)

    def test_bank_numbers_are_encrypted_in_the_database_and_masked_in_the_api(self):
        response = self.put({
            "ni_number": "ab 12 34 56 c", "tax_code": "1257l", "account_name": "S Employee",
            "sort_code": "12-34-56", "account_number": "12345678",
        })
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {
            "ni_number": "AB******C", "tax_code": "1257L", "account_name": "S Employee",
            "sort_code": "**-**-56", "account_number": "****5678", "has_bank_details": True,
            "date_of_birth": "**/**/2007", "has_date_of_birth": True, "typical_day_hours": None,
            "ni_category": "", "student_loan_plan": "", "postgrad_loan": False, "pension_opted_out": False,
            "apprentice_from": None, "prior_pay": None, "prior_tax": None,
        })
        details = PayDetails.objects.get()
        self.assertEqual(details.ni_number, "AB123456C")
        self.assertEqual(crypto.decrypt(details.sort_code_encrypted), "123456")
        self.assertEqual(crypto.decrypt(details.account_number_encrypted), "12345678")
        with connection.cursor() as cursor:
            cursor.execute("select sort_code_encrypted, account_number_encrypted from payroll_paydetails")
            raw = " ".join(cursor.fetchone())
        self.assertNotIn("12345678", raw)
        self.assertNotIn("123456", raw.replace("12345678", ""))
        got = self.client.get(reverse("employee-pay-details", args=[self.worker.pk])).json()
        self.assertEqual(got["account_number"], "****5678")

    def test_nothing_stored_yet_reads_as_empty(self):
        self.as_user(self.hr)
        data = self.client.get(reverse("employee-pay-details", args=[self.worker.pk])).json()
        self.assertEqual((data["has_bank_details"], data["sort_code"], data["ni_number"]), (False, "", ""))

    def test_wrong_looking_numbers_are_refused(self):
        for field, bad in [
            ("ni_number", "12345678"), ("ni_number", "DQ123456C"), ("ni_number", "AB123456E"), ("tax_code", "!!"),
            ("sort_code", "12-34"), ("account_number", "1234567"), ("account_number", "1234567a"),
        ]:
            self.assertEqual(self.put({field: bad}).status_code, 400, (field, bad))
        self.assertFalse(PayDetails.objects.exists() and PayDetails.objects.get().ni_number)

    def test_a_partial_update_changes_only_what_was_sent_and_empty_clears(self):
        self.put({"sort_code": "111111", "account_number": "22222222", "tax_code": "BR"})
        self.put({"tax_code": "1257L"})
        details = PayDetails.objects.get()
        self.assertEqual(crypto.decrypt(details.sort_code_encrypted), "111111")
        self.assertEqual(details.tax_code, "1257L")
        self.put({"account_number": ""})
        self.assertFalse(self.client.get(reverse("employee-pay-details", args=[self.worker.pk])).json()["has_bank_details"])

    def test_a_changed_encryption_key_is_reported_not_returned_as_junk(self):
        from django.core.exceptions import ImproperlyConfigured
        from django.test import override_settings

        self.put({"sort_code": "111111"})
        with override_settings(PAYROLL_ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="):
            with self.assertRaises(ImproperlyConfigured):
                crypto.decrypt(PayDetails.objects.get().sort_code_encrypted)

    def test_no_key_in_production_means_no_bank_details_are_stored(self):
        from django.core.exceptions import ImproperlyConfigured
        from django.test import override_settings

        with override_settings(DEBUG=False, PAYROLL_ENCRYPTION_KEY=""):
            with self.assertRaises(ImproperlyConfigured):
                crypto.encrypt("123456")


class ExtraHoursEndpointTests(ApiBase):
    def setUp(self):
        super().setUp()
        self.long = self.rec(1, "09:00", "18:00", rota=("09:00", "17:00"))
        self.url = reverse("approve-extra-hours", args=[self.long.pk])

    def test_the_shops_manager_and_admins_can_approve(self):
        for user in (self.manager_a, self.hr, self.owner):
            self.as_user(user)
            self.assertEqual(self.client.post(self.url, {"note": "Delivery"}).status_code, 201)
        self.assertEqual(HoursApproval.objects.count(), 1)

    def test_another_shops_manager_and_employees_cannot(self):
        self.as_user(self.manager_b)
        self.assertEqual(self.client.post(self.url).status_code, 404)
        self.as_user(self.worker.user)
        self.assertEqual(self.client.post(self.url).status_code, 403)
        self.assertEqual(HoursApproval.objects.count(), 0)

    def test_a_person_still_clocked_in_cannot_be_approved(self):
        open_one = AttendanceRecord.objects.create(employee=self.worker, store=self.store_a, clock_in_at=when(MON + timedelta(days=3), "09:00"))
        self.as_user(self.hr)
        self.assertEqual(self.client.post(reverse("approve-extra-hours", args=[open_one.pk])).status_code, 400)


class MyPayslipsTests(ApiBase):
    def setUp(self):
        super().setUp()
        self.rec(0, "09:00", "17:00")
        self.run = self.run_for()
        self.mine = self.slip(self.run)

    def approve(self):
        for slip in self.run.payslips.all():
            services.set_deductions(self.hr, slip, {}, True)
        from apps.payroll.models import PayslipTemplate
        services.approve_run(self.hr, self.run, PayslipTemplate.current())

    def test_nobody_sees_a_payslip_before_the_run_is_approved(self):
        self.as_user(self.worker.user)
        self.assertEqual(self.client.get(reverse("my-payslips")).json(), [])
        self.assertEqual(self.client.get(reverse("my-payslip", args=[self.mine.pk])).status_code, 404)

    def test_after_approval_each_person_sees_only_their_own(self):
        AttendanceRecord.objects.create(
            employee=self.emp_b, store=self.store_b, clock_in_at=when(MON, "09:00"), clock_out_at=when(MON, "17:00")
        )
        services.add_rate(self.hr, self.emp_b, Decimal("11.00"), MON)
        services.set_pay_details(self.hr, self.emp_b, {"date_of_birth": date(2007, 6, 1)})
        services.fill_run(self.run)
        self.approve()
        theirs = self.slip(self.run, self.emp_b)
        self.as_user(self.worker.user)
        rows = self.client.get(reverse("my-payslips")).json()
        self.assertEqual([r["id"] for r in rows], [self.mine.pk])
        self.assertEqual(self.client.get(reverse("my-payslip", args=[self.mine.pk])).json()["net_pay"], "96.00")
        self.assertEqual(self.client.get(reverse("my-payslip", args=[theirs.pk])).status_code, 404)

    def test_someone_without_an_employee_record_sees_nothing(self):
        self.as_user(self.hr)
        self.assertEqual(self.client.get(reverse("my-payslips")).json(), [])
