import json
from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.accounts.models import Role
from apps.core.testutils import days_from_today, make_user, verified_term_letter
from apps.employees import services as employee_services
from apps.stores.models import Store

from . import services
from .models import (
    CheckOutcome,
    EmployeeVisa,
    RightToWorkCheck,
    VacationPeriod,
    VisaAuditLog,
    VisaStatus,
    VisaType,
)
from .rules import Code, check_right_to_work, week_bounds

TODAY = days_from_today(0)


class VisaTestBase(APITestCase):
    def setUp(self):
        self.owner = make_user("owner@example.com", Role.OWNER)
        self.hr = make_user("hr@example.com", Role.HR)
        self.manager_a = make_user("mgr-a@example.com", Role.MANAGER)
        self.manager_b = make_user("mgr-b@example.com", Role.MANAGER)
        self.store_a = Store.objects.create(name="Shop A")
        self.store_b = Store.objects.create(name="Shop B")
        self.store_a.managers.add(self.manager_a)
        self.store_b.managers.add(self.manager_b)
        self.employee = self.hire("emp@example.com", self.store_a)

    def hire(self, email, store, start=-60):
        return employee_services.create_employee(
            user=make_user(email, Role.EMPLOYEE),
            home_store=store,
            start_date=days_from_today(start),
            created_by=self.hr,
        )

    def vt(self, name):
        return VisaType.objects.get(name=name)

    def give_visa(self, employee, type_name, *, expiry=None, start=None, check="pass", due=None, **extra):
        if type_name == "Skilled Worker":
            extra.setdefault("sponsor_reference", "CoS-1")
        visa = services.add_visa(
            employee=employee, by=self.hr, visa_type=self.vt(type_name),
            start_date=start, expiry_date=expiry, **extra,
        )
        if check:
            services.record_check(
                employee=employee, by=self.hr, visa=visa, check_type="online",
                outcome=check, checked_on=days_from_today(-5), next_check_due=due,
            )
        return visa

    def login(self, user):
        self.client.force_authenticate(user)

    def decide(self, on=TODAY, week_hours=None, employee=None):
        return check_right_to_work(employee or self.employee, on, week_hours)


class SeedDataTests(VisaTestBase):
    def test_standard_types_are_seeded(self):
        names = set(VisaType.objects.values_list("name", flat=True))
        for expected in ("British citizen", "Irish citizen", "Student", "Skilled Worker",
                         "Graduate", "Pre-settled status", "Visitor / no right to work"):
            self.assertIn(expected, names)
        student = self.vt("Student")
        self.assertEqual(student.max_weekly_hours, 20)
        self.assertTrue(student.requires_expiry)
        self.assertFalse(self.vt("British citizen").requires_expiry)
        self.assertTrue(self.vt("Skilled Worker").requires_sponsorship)
        self.assertFalse(self.vt("Visitor / no right to work").can_work)


class RightToWorkRulesTests(VisaTestBase):
    def test_no_record_means_not_allowed(self):
        decision = self.decide()
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.codes, {Code.NO_RECORD})

    def test_citizen_with_passing_check_needs_no_expiry(self):
        self.give_visa(self.employee, "British citizen")
        self.assertTrue(self.decide().allowed)
        self.assertTrue(self.decide(on=days_from_today(5000)).allowed)

    def test_no_check_on_file_blocks(self):
        self.give_visa(self.employee, "British citizen", check=None)
        self.assertEqual(self.decide().codes, {Code.CHECK_MISSING})

    def test_failed_or_follow_up_check_blocks(self):
        visa = self.give_visa(self.employee, "British citizen")
        for outcome, code in (("fail", Code.CHECK_FAILED), ("follow_up", Code.CHECK_FOLLOW_UP)):
            with self.subTest(outcome=outcome):
                services.record_check(
                    employee=self.employee, by=self.hr, visa=visa, check_type="manual",
                    outcome=outcome, checked_on=days_from_today(-1),
                )
                self.assertEqual(self.decide().codes, {code})

    def test_a_later_passing_check_clears_an_earlier_failure(self):
        visa = self.give_visa(self.employee, "British citizen", check="fail")
        self.assertFalse(self.decide().allowed)
        services.record_check(
            employee=self.employee, by=self.hr, visa=visa, check_type="manual",
            outcome="pass", checked_on=days_from_today(-1),
        )
        self.assertTrue(self.decide().allowed)

    def test_overdue_follow_up_check_blocks(self):
        visa = self.give_visa(self.employee, "British citizen", check=None)
        services.record_check(
            employee=self.employee, by=self.hr, visa=visa, check_type="online", outcome="pass",
            checked_on=days_from_today(-10), next_check_due=days_from_today(-2),
        )
        self.assertEqual(self.decide().codes, {Code.CHECK_OVERDUE})
        self.assertTrue(self.decide(on=days_from_today(-3)).allowed)

    def test_expiry_day_is_still_valid_next_day_is_not(self):
        expiry = days_from_today(10)
        self.give_visa(self.employee, "Graduate", expiry=expiry)
        self.assertTrue(self.decide(on=expiry).allowed)
        self.assertEqual(self.decide(on=days_from_today(11)).codes, {Code.EXPIRED})

    def test_rota_for_after_expiry_is_blocked_though_today_is_fine(self):
        self.give_visa(self.employee, "Graduate", expiry=days_from_today(20))
        self.assertTrue(self.decide(on=TODAY).allowed)
        self.assertFalse(self.decide(on=days_from_today(40)).allowed)

    def test_already_expired_blocks_today(self):
        self.give_visa(self.employee, "Graduate", expiry=days_from_today(-1))
        self.assertEqual(self.decide().codes, {Code.EXPIRED})

    def test_status_not_started_yet_blocks(self):
        self.give_visa(self.employee, "Graduate", start=days_from_today(7), expiry=days_from_today(300))
        self.assertEqual(self.decide().codes, {Code.NOT_YET_VALID})
        self.assertTrue(self.decide(on=days_from_today(7)).allowed)

    def test_visitor_cannot_work(self):
        self.give_visa(self.employee, "Visitor / no right to work")
        self.assertEqual(self.decide().codes, {Code.NOT_PERMITTED})

    def test_revoked_status_blocks(self):
        visa = self.give_visa(self.employee, "British citizen")
        services.revoke_visa(visa=visa, by=self.hr, reason="Home Office notice")
        self.assertEqual(self.decide().codes, {Code.REVOKED})

    def test_manual_decision_types_need_hr_to_say_yes(self):
        name = "Dependant / partner (conditions vary)"
        visa = self.give_visa(self.employee, name, expiry=days_from_today(300))
        self.assertEqual(self.decide().codes, {Code.MANUAL_DECISION})
        services.update_visa(visa=visa, by=self.hr, manual_work_allowed=False)
        self.assertEqual(self.decide().codes, {Code.MANUAL_DECISION})
        services.update_visa(visa=visa, by=self.hr, manual_work_allowed=True)
        self.assertTrue(self.decide().allowed)

    def test_sponsored_status_without_reference_blocks(self):
        EmployeeVisa.objects.create(
            employee=self.employee, visa_type=self.vt("Skilled Worker"), expiry_date=days_from_today(300)
        )
        self.assertIn(Code.SPONSOR_MISSING, self.decide().codes)

    def test_sponsored_status_allowed_with_a_reminder(self):
        self.give_visa(self.employee, "Skilled Worker", expiry=days_from_today(300))
        decision = self.decide()
        self.assertTrue(decision.allowed)
        self.assertTrue(any("sponsored" in w.lower() for w in decision.warnings))

    def test_time_limited_status_without_expiry_blocks(self):
        EmployeeVisa.objects.create(employee=self.employee, visa_type=self.vt("Graduate"))
        self.assertIn(Code.EXPIRY_MISSING, self.decide().codes)

    def test_settled_status_ignores_a_document_expiry_date(self):
        self.give_visa(
            self.employee, "Settled status (ILR / EU Settled Status)", expiry=days_from_today(-400)
        )
        self.assertTrue(self.decide().allowed)

    def test_every_problem_is_reported_not_just_the_first(self):
        self.give_visa(self.employee, "Graduate", expiry=days_from_today(-3), check=None)
        self.assertEqual(self.decide().codes, {Code.EXPIRED, Code.CHECK_MISSING})

    def test_only_the_current_status_counts(self):
        self.give_visa(self.employee, "Graduate", expiry=days_from_today(-30))
        self.assertFalse(self.decide().allowed)
        self.give_visa(self.employee, "British citizen")
        self.assertTrue(self.decide().allowed)

    def test_status_close_to_expiry_warns(self):
        self.give_visa(self.employee, "Graduate", expiry=days_from_today(30))
        decision = self.decide()
        self.assertTrue(decision.allowed)
        self.assertTrue(any("expires" in w for w in decision.warnings))

    def test_status_far_from_expiry_has_no_warning(self):
        self.give_visa(self.employee, "Graduate", expiry=days_from_today(400))
        self.assertEqual(self.decide().warnings, [])


class WeeklyHoursCapTests(VisaTestBase):
    def student(self, **extra):
        return self.give_visa(self.employee, "Student", expiry=days_from_today(300), **extra)

    def test_week_at_the_cap_is_allowed_over_it_is_not(self):
        self.student()
        self.assertTrue(self.decide(week_hours=Decimal("20")).allowed)
        over = self.decide(week_hours=Decimal("20.5"))
        self.assertFalse(over.allowed)
        self.assertEqual(over.codes, {Code.HOURS_LIMIT})
        self.assertEqual(over.weekly_hours_limit, 20)

    def test_limit_is_reported_even_when_hours_are_not_given(self):
        self.student()
        decision = self.decide()
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.weekly_hours_limit, 20)

    def test_other_statuses_have_no_cap(self):
        self.give_visa(self.employee, "Graduate", expiry=days_from_today(300))
        decision = self.decide(week_hours=Decimal("60"))
        self.assertTrue(decision.allowed)
        self.assertIsNone(decision.weekly_hours_limit)

    def test_vacation_lifts_the_cap_only_inside_the_period(self):
        visa = self.student()
        verified_term_letter(
            visa, self.hr, days_from_today(-30), days_from_today(100),
            [{"start_date": days_from_today(10), "end_date": days_from_today(20), "label": "Winter"}],
        )
        inside = self.decide(on=days_from_today(15), week_hours=Decimal("40"))
        self.assertTrue(inside.allowed)
        self.assertIsNone(inside.weekly_hours_limit)
        self.assertTrue(any("vacation" in w.lower() for w in inside.warnings))
        for outside in (days_from_today(9), days_from_today(21)):
            with self.subTest(day=outside):
                self.assertEqual(self.decide(on=outside, week_hours=Decimal("40")).codes, {Code.HOURS_LIMIT})

    def test_vacation_boundary_days_are_inside(self):
        visa = self.student()
        verified_term_letter(
            visa, self.hr, days_from_today(-30), days_from_today(100),
            [{"start_date": days_from_today(10), "end_date": days_from_today(12), "label": ""}],
        )
        for day in (10, 12):
            self.assertIsNone(self.decide(on=days_from_today(day)).weekly_hours_limit)

    def test_personal_override_beats_the_type_default(self):
        self.student(weekly_hours_cap_override=10)
        self.assertEqual(self.decide(week_hours=Decimal("15")).codes, {Code.HOURS_LIMIT})
        self.assertTrue(self.decide(week_hours=Decimal("10")).allowed)

    def test_cap_does_not_let_an_otherwise_blocked_person_through(self):
        self.give_visa(self.employee, "Student", expiry=days_from_today(-1))
        self.assertFalse(self.decide(week_hours=Decimal("5")).allowed)

    def test_week_runs_monday_to_sunday(self):
        start, end = week_bounds(date(2026, 10, 21))  # a Wednesday
        self.assertEqual((start, end), (date(2026, 10, 19), date(2026, 10, 25)))
        self.assertEqual(week_bounds(date(2026, 10, 19))[0], date(2026, 10, 19))
        self.assertEqual(week_bounds(date(2026, 10, 25))[0], date(2026, 10, 19))
        self.assertEqual(week_bounds(date(2026, 10, 26))[0], date(2026, 10, 26))


class StatusTests(VisaTestBase):
    def test_status_values(self):
        pending = self.give_visa(self.employee, "Graduate", expiry=days_from_today(300), check=None)
        self.assertEqual(pending.status_on(), VisaStatus.PENDING_CHECK)

        services.record_check(
            employee=self.employee, by=self.hr, visa=pending, check_type="online",
            outcome="fail", checked_on=days_from_today(-2),
        )
        self.assertEqual(EmployeeVisa.objects.get(pk=pending.pk).status_on(), VisaStatus.CHECK_FAILED)

        services.record_check(
            employee=self.employee, by=self.hr, visa=pending, check_type="online",
            outcome="pass", checked_on=days_from_today(-1),
        )
        self.assertEqual(EmployeeVisa.objects.get(pk=pending.pk).status_on(), VisaStatus.VALID)

        fresh = EmployeeVisa.objects.get(pk=pending.pk)
        self.assertEqual(fresh.status_on(days_from_today(250)), VisaStatus.EXPIRING_SOON)
        self.assertEqual(fresh.status_on(days_from_today(301)), VisaStatus.EXPIRED)

        services.revoke_visa(visa=fresh, by=self.hr)
        self.assertEqual(EmployeeVisa.objects.get(pk=pending.pk).status_on(), VisaStatus.REVOKED)


class VisaAccessTests(VisaTestBase):
    """Immigration data is Owner/HR only."""

    def setUp(self):
        super().setUp()
        self.visa = self.give_visa(self.employee, "Student", expiry=days_from_today(300))

    def endpoints(self):
        emp, visa = self.employee.pk, self.visa.pk
        return [
            ("get", reverse("employee-visas", args=[emp])),
            ("post", reverse("employee-visas", args=[emp])),
            ("get", reverse("employee-rtw-checks", args=[emp])),
            ("post", reverse("employee-rtw-checks", args=[emp])),
            ("get", reverse("visa-expiring")),
            ("get", reverse("visa-detail", args=[visa])),
            ("patch", reverse("visa-detail", args=[visa])),
            ("post", reverse("visa-revoke", args=[visa])),
            ("get", reverse("visa-vacations", args=[visa])),
            ("post", reverse("visa-vacations", args=[visa])),
            ("get", reverse("visa-type-list")),
        ]

    def test_managers_and_employees_are_refused_everywhere(self):
        for user in (self.manager_a, self.employee.user):
            self.login(user)
            for method, url in self.endpoints():
                with self.subTest(user=user.role, method=method, url=url):
                    response = getattr(self.client, method)(url, {}, format="json")
                    self.assertEqual(response.status_code, 403)

    def test_anonymous_gets_401_everywhere(self):
        self.client.force_authenticate(None)
        for method, url in self.endpoints():
            with self.subTest(method=method, url=url):
                self.assertEqual(getattr(self.client, method)(url, {}, format="json").status_code, 401)

    def test_owner_and_hr_can_read(self):
        for user in (self.owner, self.hr):
            self.login(user)
            self.assertEqual(self.client.get(reverse("employee-visas", args=[self.employee.pk])).status_code, 200)

    def test_manager_cannot_see_visa_data_through_the_employee_endpoints(self):
        self.login(self.manager_a)
        profile = self.client.get(reverse("employee-detail", args=[self.employee.pk]))
        self.assertEqual(profile.status_code, 200)
        self.assertNotIn("visa", json.dumps(profile.data).lower())


class VisaTypeApiTests(VisaTestBase):
    def test_hr_can_list_and_edit_the_rules(self):
        self.login(self.hr)
        listing = self.client.get(reverse("visa-type-list"))
        self.assertEqual(listing.status_code, 200)
        student = next(row for row in listing.data if row["name"] == "Student")
        response = self.client.patch(reverse("visa-type-detail", args=[student["id"]]), {"max_weekly_hours": 10})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.vt("Student").max_weekly_hours, 10)

    def test_changing_the_rule_changes_the_decision(self):
        self.give_visa(self.employee, "Student", expiry=days_from_today(300))
        self.assertTrue(self.decide(week_hours=Decimal("15")).allowed)
        VisaType.objects.filter(name="Student").update(max_weekly_hours=10)
        self.assertFalse(self.decide(week_hours=Decimal("15")).allowed)

    def test_hr_can_add_a_type_but_not_delete_one(self):
        self.login(self.hr)
        created = self.client.post(reverse("visa-type-list"), {"name": "Youth Mobility"})
        self.assertEqual(created.status_code, 201)
        url = reverse("visa-type-detail", args=[created.data["id"]])
        self.assertEqual(self.client.delete(url).status_code, 405)

    def test_cap_must_be_sensible(self):
        self.login(self.hr)
        student = self.vt("Student")
        for bad in (0, -5, 500):
            response = self.client.patch(reverse("visa-type-detail", args=[student.pk]), {"max_weekly_hours": bad})
            self.assertEqual(response.status_code, 400, bad)


class VisaManagementTests(VisaTestBase):
    def url(self):
        return reverse("employee-visas", args=[self.employee.pk])

    def payload(self, type_name="Graduate", **extra):
        data = {"visa_type": self.vt(type_name).pk, "expiry_date": days_from_today(300).isoformat()}
        data.update(extra)
        return data

    def test_adding_a_status_is_pending_until_a_check_is_recorded(self):
        self.login(self.hr)
        response = self.client.post(self.url(), self.payload())
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], "pending_check")
        self.assertTrue(response.data["is_current"])
        self.assertEqual(response.data["visa_type"]["name"], "Graduate")
        self.assertFalse(self.decide().allowed)

    def test_a_new_status_replaces_the_old_one_and_keeps_history(self):
        self.login(self.hr)
        first = self.client.post(self.url(), self.payload("Student"))
        second = self.client.post(self.url(), self.payload("Graduate"))
        self.assertEqual(second.status_code, 201)
        history = self.client.get(self.url())
        self.assertEqual([row["visa_type"]["name"] for row in history.data], ["Graduate", "Student"])
        self.assertEqual([row["is_current"] for row in history.data], [True, False])
        self.assertFalse(EmployeeVisa.objects.get(pk=first.data["id"]).is_current)

    def test_database_allows_only_one_current_status(self):
        self.give_visa(self.employee, "British citizen")
        with self.assertRaises(IntegrityError), transaction.atomic():
            EmployeeVisa.objects.create(employee=self.employee, visa_type=self.vt("Graduate"), is_current=True)

    def test_validation_rules(self):
        self.login(self.hr)
        cases = {
            "student needs an expiry": self.payload("Student", expiry_date=None),
            "skilled worker needs a sponsor": self.payload("Skilled Worker"),
            "expiry before start": self.payload("Graduate", start_date=days_from_today(400).isoformat()),
            "manual flag on a normal type": self.payload("Graduate", manual_work_allowed=True),
            "hours override out of range": self.payload("Student", weekly_hours_cap_override=0),
            "unknown type": self.payload("Graduate", visa_type=99999),
        }
        for label, data in cases.items():
            with self.subTest(label):
                self.assertEqual(self.client.post(self.url(), data).status_code, 400)
        self.assertFalse(EmployeeVisa.objects.filter(employee=self.employee).exists())

    def test_citizen_needs_no_expiry(self):
        self.login(self.hr)
        response = self.client.post(self.url(), {"visa_type": self.vt("British citizen").pk})
        self.assertEqual(response.status_code, 201, response.data)

    def test_switched_off_type_cannot_be_used(self):
        VisaType.objects.filter(name="Graduate").update(is_active=False)
        self.login(self.hr)
        self.assertEqual(self.client.post(self.url(), self.payload()).status_code, 400)

    def test_unknown_employee_is_404(self):
        self.login(self.hr)
        self.assertEqual(self.client.get(reverse("employee-visas", args=[99999])).status_code, 404)

    def test_hr_can_correct_details_but_not_the_type_or_employee(self):
        visa = self.give_visa(self.employee, "Graduate", expiry=days_from_today(300))
        other = self.hire("other@example.com", self.store_a)
        self.login(self.hr)
        url = reverse("visa-detail", args=[visa.pk])
        response = self.client.patch(url, {
            "expiry_date": days_from_today(500).isoformat(),
            "visa_type": self.vt("Student").pk,
            "employee": other.pk,
        })
        self.assertEqual(response.status_code, 200, response.data)
        visa.refresh_from_db()
        self.assertEqual(visa.expiry_date, days_from_today(500))
        self.assertEqual(visa.visa_type.name, "Graduate")
        self.assertEqual(visa.employee_id, self.employee.pk)

    def test_a_correction_that_breaks_the_rules_is_rejected_and_not_saved(self):
        visa = self.give_visa(self.employee, "Student", expiry=days_from_today(300))
        self.login(self.hr)
        response = self.client.patch(reverse("visa-detail", args=[visa.pk]), {"expiry_date": None})
        self.assertEqual(response.status_code, 400)
        visa.refresh_from_db()
        self.assertEqual(visa.expiry_date, days_from_today(300))

    def test_revoking_blocks_work_and_cannot_be_repeated(self):
        visa = self.give_visa(self.employee, "British citizen")
        self.assertTrue(self.decide().allowed)
        self.login(self.hr)
        url = reverse("visa-revoke", args=[visa.pk])
        response = self.client.post(url, {"reason": "Notified by the Home Office"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "revoked")
        self.assertEqual(response.data["revoked_on"], str(TODAY))
        self.assertFalse(self.decide().allowed)
        self.assertEqual(self.client.post(url, {}).status_code, 400)

    def test_vacations_can_be_listed_but_not_added_by_hand(self):
        visa = self.give_visa(self.employee, "Student", expiry=days_from_today(300))
        verified_term_letter(
            visa, self.hr, days_from_today(-30), days_from_today(100),
            [{"start_date": days_from_today(10), "end_date": days_from_today(20), "label": "Christmas"}],
        )
        self.login(self.hr)
        url = reverse("visa-vacations", args=[visa.pk])
        self.assertEqual(len(self.client.get(url).data), 1)
        self.assertEqual(self.client.post(url, {
            "start_date": days_from_today(30).isoformat(), "end_date": days_from_today(40).isoformat(),
        }).status_code, 405)
        self.assertEqual(VacationPeriod.objects.filter(visa=visa).count(), 1)

    def test_actions_are_written_to_the_audit_log_without_document_values(self):
        self.login(self.hr)
        created = self.client.post(self.url(), self.payload(work_conditions="Cannot work nights"))
        visa_id = created.data["id"]
        self.client.get(reverse("visa-detail", args=[visa_id]))
        self.client.post(reverse("visa-revoke", args=[visa_id]), {})
        actions = list(VisaAuditLog.objects.order_by("id").values_list("action", flat=True))
        self.assertEqual(actions, ["visa_added", "visa_viewed", "visa_revoked"])
        entry = VisaAuditLog.objects.first()
        self.assertEqual(entry.user, self.hr)
        self.assertEqual(entry.employee, self.employee)
        self.assertNotIn("nights", " ".join(VisaAuditLog.objects.values_list("detail", flat=True)))


class RightToWorkCheckApiTests(VisaTestBase):
    def setUp(self):
        super().setUp()
        self.visa = self.give_visa(self.employee, "Graduate", expiry=days_from_today(300), check=None)
        self.url = reverse("employee-rtw-checks", args=[self.employee.pk])

    def payload(self, **extra):
        data = {"visa": self.visa.pk, "check_type": "online", "outcome": "pass"}
        data.update(extra)
        return data

    def test_recording_a_pass_makes_the_status_valid_and_records_who_did_it(self):
        self.login(self.hr)
        response = self.client.post(self.url, self.payload(evidence_reference="Folder 12 / RTW-0007"))
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["checked_on"], str(TODAY))
        self.assertEqual(response.data["checked_by"], self.hr.pk)
        self.assertTrue(self.decide().allowed)
        self.assertEqual(EmployeeVisa.objects.get(pk=self.visa.pk).status_on(), VisaStatus.VALID)

    def test_checked_by_cannot_be_set_by_the_caller(self):
        self.login(self.hr)
        response = self.client.post(self.url, self.payload(checked_by=self.owner.pk))
        self.assertEqual(response.data["checked_by"], self.hr.pk)

    def test_history_is_listed_newest_first(self):
        self.login(self.hr)
        self.client.post(self.url, self.payload(outcome="fail", checked_on=days_from_today(-3).isoformat()))
        self.client.post(self.url, self.payload(checked_on=days_from_today(-1).isoformat()))
        outcomes = [row["outcome"] for row in self.client.get(self.url).data]
        self.assertEqual(outcomes, ["pass", "fail"])

    def test_validation_rules(self):
        other = self.hire("other@example.com", self.store_a)
        other_visa = self.give_visa(other, "Graduate", expiry=days_from_today(300), check=None)
        old = self.give_visa(self.employee, "Student", expiry=days_from_today(300), check=None)  # old becomes history
        self.login(self.hr)
        cases = {
            "someone else's status": self.payload(visa=other_visa.pk),
            "a status that is no longer current": self.payload(visa=self.visa.pk),
            "future date": self.payload(visa=old.pk, checked_on=days_from_today(1).isoformat()),
            "next check not after this one": self.payload(
                visa=old.pk, checked_on=days_from_today(-1).isoformat(), next_check_due=days_from_today(-1).isoformat()
            ),
            "bad outcome": self.payload(visa=old.pk, outcome="maybe"),
            "bad type": self.payload(visa=old.pk, check_type="telepathy"),
        }
        for label, data in cases.items():
            with self.subTest(label):
                self.assertEqual(self.client.post(self.url, data).status_code, 400)
        self.assertEqual(RightToWorkCheck.objects.count(), 0)

    def test_checks_cannot_be_edited_or_deleted(self):
        self.login(self.hr)
        created = self.client.post(self.url, self.payload())
        for method in ("put", "patch", "delete"):
            with self.subTest(method):
                self.assertEqual(getattr(self.client, method)(self.url, {}).status_code, 405)
        self.assertEqual(self.client.get(self.url).data[0]["id"], created.data["id"])

    def test_outcome_choices_are_the_known_ones(self):
        self.assertEqual({c.value for c in CheckOutcome}, {"pass", "fail", "follow_up"})


class ExpiringVisasTests(VisaTestBase):
    def setUp(self):
        super().setUp()
        self.soon = self.give_visa(self.hire("soon@example.com", self.store_a), "Graduate", expiry=days_from_today(20))
        self.later = self.give_visa(self.hire("later@example.com", self.store_a), "Graduate", expiry=days_from_today(50))
        self.far = self.give_visa(self.hire("far@example.com", self.store_a), "Graduate", expiry=days_from_today(200))
        self.gone = self.give_visa(self.hire("gone@example.com", self.store_a), "Graduate", expiry=days_from_today(-4))
        self.login(self.hr)

    def ids(self, days=60):
        response = self.client.get(reverse("visa-expiring"), {"days": days})
        self.assertEqual(response.status_code, 200)
        return [row["id"] for row in response.data]

    def test_lists_expired_then_soonest_within_the_window(self):
        self.assertEqual(self.ids(60), [self.gone.pk, self.soon.pk, self.later.pk])
        self.assertEqual(self.ids(30), [self.gone.pk, self.soon.pk])
        self.assertEqual(self.ids(250), [self.gone.pk, self.soon.pk, self.later.pk, self.far.pk])

    def test_rows_show_status_and_who(self):
        rows = {row["id"]: row for row in self.client.get(reverse("visa-expiring"), {"days": 60}).data}
        self.assertEqual(rows[self.gone.pk]["status"], "expired")
        self.assertEqual(rows[self.soon.pk]["status"], "expiring_soon")
        self.assertRegex(rows[self.soon.pk]["employee"]["employee_number"], r"^EMP-\d{4}$")

    def test_default_window_is_sixty_days(self):
        response = self.client.get(reverse("visa-expiring"))
        self.assertEqual({row["id"] for row in response.data}, {self.gone.pk, self.soon.pk, self.later.pk})

    def test_leavers_revoked_old_and_non_expiring_statuses_are_left_out(self):
        self.soon.employee.end_date = days_from_today(-1)
        self.soon.employee.save()
        services.revoke_visa(visa=self.later, by=self.hr)
        self.give_visa(self.gone.employee, "British citizen")  # replaces the expired Graduate status
        self.assertEqual(self.ids(60), [])

    def test_days_must_be_a_sensible_number(self):
        for bad in ("abc", "0", "-3", "400"):
            with self.subTest(bad):
                self.assertEqual(self.client.get(reverse("visa-expiring"), {"days": bad}).status_code, 400)

    def test_management_command_reports_the_same_people(self):
        out = StringIO()
        call_command("check_visa_expiry", "--days", "60", stdout=out)
        text = out.getvalue()
        self.assertIn("soon@example.com", text)
        self.assertIn("later@example.com", text)
        self.assertIn("gone@example.com", text)
        self.assertIn("EXPIRED 4 day(s) ago", text)
        self.assertNotIn("far@example.com", text)
        self.assertIn("3 status(es) need attention.", text)

    def test_management_command_when_nothing_is_due(self):
        EmployeeVisa.objects.update(expiry_date=days_from_today(900))
        out = StringIO()
        call_command("check_visa_expiry", "--days", "30", stdout=out)
        self.assertIn("No statuses expire within 30 days.", out.getvalue())


class CanWorkApiTests(VisaTestBase):
    def url(self, employee=None):
        return reverse("employee-can-work", args=[(employee or self.employee).pk])

    def test_hr_gets_full_detail(self):
        self.give_visa(self.employee, "Student", expiry=days_from_today(300))
        self.login(self.hr)
        response = self.client.get(self.url(), {"week_hours": "25"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["allowed"])
        self.assertEqual(response.data["reasons"][0]["code"], "hours_limit_exceeded")
        self.assertEqual(response.data["weekly_hours_limit"], 20)
        self.assertEqual(response.data["date"], TODAY)

    def test_manager_of_the_employees_store_gets_yes_or_no_and_the_limit(self):
        self.give_visa(self.employee, "Student", expiry=days_from_today(300))
        self.login(self.manager_a)
        ok = self.client.get(self.url(), {"week_hours": "18"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(dict(ok.data), {
            "employee": self.employee.pk, "date": TODAY, "allowed": True,
            "weekly_hours_limit": 20, "paperwork_needed": True, "message": "",
        })
        over = self.client.get(self.url(), {"week_hours": "25"})
        self.assertFalse(over.data["allowed"])
        self.assertEqual(over.data["message"], "Over the weekly hours limit of 20.")

    def test_manager_never_sees_why_someone_is_not_cleared(self):
        self.give_visa(self.employee, "Skilled Worker", expiry=days_from_today(-2), check="fail")
        self.login(self.manager_a)
        response = self.client.get(self.url())
        self.assertFalse(response.data["allowed"])
        self.assertEqual(response.data["message"], "Not cleared to work. Please contact HR.")
        self.assertNotIn("reasons", response.data)
        self.assertNotIn("warnings", response.data)
        body = json.dumps(response.data, default=str).lower()
        for leak in ("skilled", "expire", "sponsor", "fail", "check_", "visa", "student"):
            self.assertNotIn(leak, body)

    def test_a_person_with_no_record_is_not_cleared(self):
        self.login(self.manager_a)
        self.assertFalse(self.client.get(self.url()).data["allowed"])

    def test_manager_of_another_store_cannot_ask(self):
        self.give_visa(self.employee, "British citizen")
        self.login(self.manager_b)
        self.assertEqual(self.client.get(self.url()).status_code, 404)

    def test_employee_and_anonymous_are_refused(self):
        self.login(self.employee.user)
        self.assertEqual(self.client.get(self.url()).status_code, 403)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.url()).status_code, 401)

    def test_unknown_employee_is_404(self):
        self.login(self.hr)
        self.assertEqual(self.client.get(reverse("employee-can-work", args=[99999])).status_code, 404)

    def test_date_matters(self):
        self.give_visa(self.employee, "Graduate", expiry=days_from_today(30))
        self.login(self.hr)
        today = self.client.get(self.url())
        later = self.client.get(self.url(), {"date": days_from_today(60).isoformat()})
        self.assertTrue(today.data["allowed"])
        self.assertFalse(later.data["allowed"])
        self.assertEqual(later.data["reasons"][0]["code"], "expired")

    def test_bad_input_is_rejected(self):
        self.login(self.hr)
        for params in ({"week_hours": "abc"}, {"week_hours": "-1"}, {"week_hours": "200"}, {"date": "not-a-date"}):
            with self.subTest(params):
                self.assertEqual(self.client.get(self.url(), params).status_code, 400)

    def test_visiting_staff_can_be_checked_by_the_destination_manager(self):
        """A manager can ask about anyone they can see, including approved cover staff."""
        from apps.employees.services import request_cover

        self.give_visa(self.employee, "British citizen")
        self.login(self.manager_b)
        self.assertEqual(self.client.get(self.url()).status_code, 404)  # not at Shop B yet

        request_cover(
            employee=self.employee, store=self.store_b, valid_from=TODAY,
            valid_to=days_from_today(3), reason="", requested_by=self.hr,
        )
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["allowed"])
