import re

from django.urls import reverse
from rest_framework.test import APITestCase

from apps.accounts.models import Role
from apps.core.testutils import days_from_today, make_user
from apps.stores.models import Store

from . import services
from .models import AssignmentKind, AssignmentStatus, EmployeeProfile, StoreAssignment


class EmployeeTestBase(APITestCase):
    def setUp(self):
        self.owner = make_user("owner@example.com", Role.OWNER)
        self.hr = make_user("hr@example.com", Role.HR)
        self.manager_a = make_user("mgr-a@example.com", Role.MANAGER)
        self.manager_b = make_user("mgr-b@example.com", Role.MANAGER)
        self.manager_c = make_user("mgr-c@example.com", Role.MANAGER)
        self.store_a = Store.objects.create(name="Shop A")
        self.store_b = Store.objects.create(name="Shop B")
        self.store_c = Store.objects.create(name="Shop C")
        self.store_a.managers.add(self.manager_a)
        self.store_b.managers.add(self.manager_b)
        self.store_c.managers.add(self.manager_c)

    def hire(self, email, store, start=-30):
        return services.create_employee(
            user=make_user(email, Role.EMPLOYEE),
            home_store=store,
            start_date=days_from_today(start),
            created_by=self.hr,
        )

    def login(self, user):
        self.client.force_authenticate(user)


class EmployeeManagementTests(EmployeeTestBase):
    def payload(self, user, store, **extra):
        data = {
            "user": user.pk,
            "home_store": store.pk,
            "start_date": days_from_today(0).isoformat(),
            "phone": "07700 900123",
            "job_title": "Cashier",
        }
        data.update(extra)
        return data

    def test_hr_creates_employee_with_home_store(self):
        user = make_user("new@example.com", Role.EMPLOYEE, first_name="Sam", last_name="Patel")
        self.login(self.hr)
        response = self.client.post(reverse("employee-list"), self.payload(user, self.store_a))
        self.assertEqual(response.status_code, 201, response.data)
        self.assertRegex(response.data["employee_number"], r"^EMP-\d{4}$")
        self.assertEqual(response.data["home_store"]["id"], self.store_a.id)
        self.assertEqual(response.data["user"]["email"], "new@example.com")
        assignment = StoreAssignment.objects.get(employee__user=user)
        self.assertEqual(assignment.kind, AssignmentKind.HOME)
        self.assertEqual(assignment.status, AssignmentStatus.APPROVED)
        self.assertIsNone(assignment.valid_to)

    def test_employee_numbers_are_unique(self):
        first = self.hire("e1@example.com", self.store_a)
        second = self.hire("e2@example.com", self.store_a)
        self.assertNotEqual(first.employee_number, second.employee_number)
        self.assertTrue(re.match(r"^EMP-\d{4}$", second.employee_number))

    def test_manager_cannot_create_employee(self):
        user = make_user("new@example.com")
        self.login(self.manager_a)
        response = self.client.post(reverse("employee-list"), self.payload(user, self.store_a))
        self.assertEqual(response.status_code, 403)

    def test_owner_and_hr_accounts_cannot_get_a_profile(self):
        self.login(self.hr)
        response = self.client.post(reverse("employee-list"), self.payload(self.owner, self.store_a))
        self.assertEqual(response.status_code, 400)

    def test_duplicate_profile_rejected(self):
        profile = self.hire("emp@example.com", self.store_a)
        self.login(self.hr)
        response = self.client.post(reverse("employee-list"), self.payload(profile.user, self.store_b))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(EmployeeProfile.objects.filter(user=profile.user).count(), 1)

    def test_inactive_store_rejected_as_home_store(self):
        self.store_c.is_active = False
        self.store_c.save()
        user = make_user("new@example.com")
        self.login(self.hr)
        response = self.client.post(reverse("employee-list"), self.payload(user, self.store_c))
        self.assertEqual(response.status_code, 400)

    def test_hr_can_update_phone_and_end_date(self):
        profile = self.hire("emp@example.com", self.store_a)
        self.login(self.hr)
        url = reverse("employee-detail", args=[profile.pk])
        response = self.client.patch(url, {"phone": "07000 111222", "end_date": days_from_today(-1).isoformat()})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["phone"], "07000 111222")
        self.assertFalse(response.data["is_current"])

    def test_end_date_before_start_rejected(self):
        profile = self.hire("emp@example.com", self.store_a, start=-10)
        self.login(self.hr)
        response = self.client.patch(
            reverse("employee-detail", args=[profile.pk]), {"end_date": days_from_today(-20).isoformat()}
        )
        self.assertEqual(response.status_code, 400)

    def test_manager_cannot_update_employee(self):
        profile = self.hire("emp@example.com", self.store_a)
        self.login(self.manager_a)
        response = self.client.patch(reverse("employee-detail", args=[profile.pk]), {"phone": "1"})
        self.assertEqual(response.status_code, 403)

    def test_user_and_email_cannot_be_changed_via_patch(self):
        profile = self.hire("emp@example.com", self.store_a)
        other = make_user("other@example.com")
        self.login(self.hr)
        self.client.patch(reverse("employee-detail", args=[profile.pk]), {"user": other.pk})
        profile.refresh_from_db()
        self.assertNotEqual(profile.user_id, other.pk)

    def test_employees_cannot_be_deleted(self):
        profile = self.hire("emp@example.com", self.store_a)
        self.login(self.owner)
        self.assertEqual(self.client.delete(reverse("employee-detail", args=[profile.pk])).status_code, 405)


class EmployeeVisibilityTests(EmployeeTestBase):
    def setUp(self):
        super().setUp()
        self.emp_a = self.hire("emp-a@example.com", self.store_a)
        self.emp_b = self.hire("emp-b@example.com", self.store_b)

    def emails(self, user):
        self.login(user)
        response = self.client.get(reverse("employee-list"))
        self.assertEqual(response.status_code, 200)
        return {item["user"]["email"] for item in response.data}

    def test_owner_and_hr_see_everyone(self):
        expected = {"emp-a@example.com", "emp-b@example.com"}
        self.assertEqual(self.emails(self.owner), expected)
        self.assertEqual(self.emails(self.hr), expected)

    def test_manager_sees_only_their_stores_staff(self):
        self.assertEqual(self.emails(self.manager_a), {"emp-a@example.com"})
        self.assertEqual(self.emails(self.manager_b), {"emp-b@example.com"})
        self.assertEqual(self.emails(self.manager_c), set())

    def test_manager_cannot_open_another_stores_employee(self):
        self.login(self.manager_a)
        self.assertEqual(self.client.get(reverse("employee-detail", args=[self.emp_b.pk])).status_code, 404)

    def test_employee_sees_only_themselves(self):
        self.assertEqual(self.emails(self.emp_a.user), {"emp-a@example.com"})
        self.login(self.emp_a.user)
        self.assertEqual(self.client.get(reverse("employee-detail", args=[self.emp_b.pk])).status_code, 404)

    def test_me_returns_own_profile(self):
        self.login(self.emp_a.user)
        response = self.client.get(reverse("employee-me"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], self.emp_a.pk)
        self.assertEqual(response.data["home_store"]["name"], "Shop A")

    def test_me_is_404_without_a_profile(self):
        self.login(self.owner)
        self.assertEqual(self.client.get(reverse("employee-me")).status_code, 404)

    def test_anonymous_gets_401(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(reverse("employee-list")).status_code, 401)


class HomeStoreTransferTests(EmployeeTestBase):
    def setUp(self):
        super().setUp()
        self.profile = self.hire("emp@example.com", self.store_a, start=-30)

    def transfer(self, store, **extra):
        return self.client.post(
            reverse("employee-transfer", args=[self.profile.pk]), {"store": store.pk, **extra}
        )

    def test_transfer_from_today_keeps_history(self):
        self.login(self.hr)
        response = self.transfer(self.store_b)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["home_store"]["id"], self.store_b.id)
        self.assertEqual(services.home_store_on(self.profile, days_from_today(-1)), self.store_a)
        self.assertEqual(services.home_store_on(self.profile, days_from_today(0)), self.store_b)
        old = StoreAssignment.objects.get(employee=self.profile, store=self.store_a)
        self.assertEqual(old.valid_to, days_from_today(-1))

    def test_future_transfer_switches_on_the_date(self):
        self.login(self.hr)
        effective = days_from_today(5)
        response = self.transfer(self.store_b, effective_date=effective.isoformat())
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["home_store"]["id"], self.store_a.id)  # still A today
        self.assertEqual(services.home_store_on(self.profile, days_from_today(4)), self.store_a)
        self.assertEqual(services.home_store_on(self.profile, effective), self.store_b)

    def test_transfer_in_the_past_rejected(self):
        self.login(self.hr)
        response = self.transfer(self.store_b, effective_date=days_from_today(-1).isoformat())
        self.assertEqual(response.status_code, 400)

    def test_transfer_to_same_store_rejected(self):
        self.login(self.hr)
        self.assertEqual(self.transfer(self.store_a).status_code, 400)

    def test_transfer_to_inactive_store_rejected(self):
        self.store_b.is_active = False
        self.store_b.save()
        self.login(self.hr)
        self.assertEqual(self.transfer(self.store_b).status_code, 400)

    def test_transfer_on_the_day_home_store_began_rejected(self):
        fresh = self.hire("fresh@example.com", self.store_a, start=0)
        self.login(self.hr)
        response = self.client.post(
            reverse("employee-transfer", args=[fresh.pk]), {"store": self.store_b.pk}
        )
        self.assertEqual(response.status_code, 400)

    def test_manager_cannot_transfer(self):
        self.login(self.manager_a)
        self.assertEqual(self.transfer(self.store_b).status_code, 403)

    def test_only_one_open_ended_home_store_per_employee(self):
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError), transaction.atomic():
            StoreAssignment.objects.create(
                employee=self.profile, store=self.store_b, kind=AssignmentKind.HOME,
                status=AssignmentStatus.APPROVED, valid_from=days_from_today(0),
            )


class CoverRequestTests(EmployeeTestBase):
    def setUp(self):
        super().setUp()
        self.employee = self.hire("emp-a@example.com", self.store_a)  # home: Shop A
        self.other = self.hire("emp-b@example.com", self.store_b)      # home: Shop B

    def request(self, user, store=None, start=3, end=5, employee=None, **extra):
        self.login(user)
        data = {
            "employee": (employee or self.employee).pk,
            "store": (store or self.store_b).pk,
            "valid_from": days_from_today(start).isoformat(),
            "valid_to": days_from_today(end).isoformat(),
        }
        data.update(extra)
        return self.client.post(reverse("cover-request-list"), data)

    def act(self, user, request_id, action, **data):
        self.login(user)
        return self.client.post(reverse(f"cover-request-{action}", args=[request_id]), data)

    def can_work_at(self, store, day):
        return services.stores_for_employee_on(self.employee, days_from_today(day)).filter(pk=store.pk).exists()

    # --- the two-manager approval flow ---

    def test_home_manager_asks_then_destination_manager_approves(self):
        response = self.request(self.manager_a)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], "pending")
        self.assertEqual(response.data["home_approved_by"], self.manager_a.pk)
        self.assertIsNone(response.data["destination_approved_by"])
        self.assertEqual(response.data["home_store"]["id"], self.store_a.id)
        self.assertFalse(self.can_work_at(self.store_b, 4))  # not cleared yet

        approved = self.act(self.manager_b, response.data["id"], "approve")
        self.assertEqual(approved.status_code, 200, approved.data)
        self.assertEqual(approved.data["status"], "approved")
        self.assertEqual(approved.data["destination_approved_by"], self.manager_b.pk)

        self.assertTrue(self.can_work_at(self.store_b, 3))
        self.assertTrue(self.can_work_at(self.store_b, 5))
        self.assertFalse(self.can_work_at(self.store_b, 2))
        self.assertFalse(self.can_work_at(self.store_b, 6))
        self.assertTrue(self.can_work_at(self.store_a, 4))  # home store still works

    def test_destination_manager_asks_then_home_manager_approves(self):
        response = self.request(self.manager_b)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], "pending")
        self.assertEqual(response.data["destination_approved_by"], self.manager_b.pk)
        self.assertIsNone(response.data["home_approved_by"])

        approved = self.act(self.manager_a, response.data["id"], "approve")
        self.assertEqual(approved.data["status"], "approved")

    def test_hr_request_is_approved_for_both_sides_immediately(self):
        response = self.request(self.hr)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "approved")
        self.assertTrue(self.can_work_at(self.store_b, 4))

    def test_manager_of_both_stores_approves_both_sides(self):
        both = make_user("both@example.com", Role.MANAGER)
        self.store_a.managers.add(both)
        self.store_b.managers.add(both)
        self.assertEqual(self.request(both).data["status"], "approved")

    def test_same_manager_cannot_approve_twice(self):
        created = self.request(self.manager_a)
        response = self.act(self.manager_a, created.data["id"], "approve")
        self.assertEqual(response.status_code, 400)

    def test_destination_cannot_approve_the_home_side(self):
        """Each manager only ever fills in their own store's approval."""
        created = self.request(self.manager_a)
        self.act(self.manager_b, created.data["id"], "approve")
        row = StoreAssignment.objects.get(pk=created.data["id"])
        self.assertEqual(row.home_approved_by, self.manager_a)
        self.assertEqual(row.destination_approved_by, self.manager_b)

    def test_unrelated_manager_cannot_request_see_or_approve(self):
        self.assertEqual(self.request(self.manager_c).status_code, 403)
        created = self.request(self.manager_a)
        self.login(self.manager_c)
        self.assertEqual(
            self.client.get(reverse("cover-request-detail", args=[created.data["id"]])).status_code, 404
        )
        self.assertEqual(self.act(self.manager_c, created.data["id"], "approve").status_code, 404)

    def test_employee_cannot_create_or_approve(self):
        self.assertEqual(self.request(self.employee.user).status_code, 403)
        created = self.request(self.manager_a)
        self.assertEqual(self.act(self.employee.user, created.data["id"], "approve").status_code, 403)

    # --- rejecting and cancelling ---

    def test_either_side_can_reject_and_it_is_final(self):
        created = self.request(self.manager_a)
        rejected = self.act(self.manager_b, created.data["id"], "reject", reason="We are short-staffed")
        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(rejected.data["status"], "rejected")
        self.assertEqual(rejected.data["rejected_by"], self.manager_b.pk)
        self.assertEqual(rejected.data["rejection_reason"], "We are short-staffed")
        self.assertEqual(self.act(self.manager_b, created.data["id"], "approve").status_code, 400)
        self.assertFalse(self.can_work_at(self.store_b, 4))

    def test_requester_and_hr_can_cancel_but_other_manager_cannot(self):
        created = self.request(self.manager_a)
        self.assertEqual(self.act(self.manager_b, created.data["id"], "cancel").status_code, 403)
        cancelled = self.act(self.manager_a, created.data["id"], "cancel")
        self.assertEqual(cancelled.data["status"], "cancelled")
        self.assertEqual(self.act(self.manager_a, created.data["id"], "cancel").status_code, 400)

        again = self.request(self.manager_a)
        self.assertEqual(self.act(self.hr, again.data["id"], "cancel").status_code, 200)

    def test_cancelling_an_approved_request_removes_the_clearance(self):
        created = self.request(self.hr)
        self.assertTrue(self.can_work_at(self.store_b, 4))
        self.act(self.hr, created.data["id"], "cancel")
        self.assertFalse(self.can_work_at(self.store_b, 4))

    # --- validation ---

    def test_destination_must_differ_from_home_store(self):
        self.assertEqual(self.request(self.manager_a, store=self.store_a).status_code, 400)

    def test_end_before_start_rejected(self):
        self.assertEqual(self.request(self.manager_a, start=5, end=3).status_code, 400)

    def test_period_entirely_in_the_past_rejected(self):
        self.assertEqual(self.request(self.manager_a, start=-5, end=-2).status_code, 400)

    def test_period_already_under_way_is_allowed(self):
        self.assertEqual(self.request(self.manager_a, start=-1, end=2).status_code, 201)

    def test_overlapping_request_for_same_store_rejected(self):
        self.assertEqual(self.request(self.manager_a, start=3, end=5).status_code, 201)
        self.assertEqual(self.request(self.manager_a, start=5, end=7).status_code, 400)
        self.assertEqual(self.request(self.manager_a, start=1, end=3).status_code, 400)
        self.assertEqual(self.request(self.manager_a, start=6, end=8).status_code, 201)

    def test_a_rejected_request_does_not_block_a_new_one(self):
        first = self.request(self.manager_a)
        self.act(self.manager_b, first.data["id"], "reject")
        self.assertEqual(self.request(self.manager_a).status_code, 201)

    def test_inactive_store_rejected(self):
        self.store_b.is_active = False
        self.store_b.save()
        self.assertEqual(self.request(self.manager_a).status_code, 400)

    def test_employee_who_has_left_rejected(self):
        self.employee.end_date = days_from_today(-1)
        self.employee.save()
        self.assertEqual(self.request(self.manager_a).status_code, 400)

    def test_deactivated_user_rejected(self):
        self.employee.user.is_active = False
        self.employee.user.save()
        self.assertEqual(self.request(self.manager_a).status_code, 400)

    def test_cover_follows_the_home_store_on_the_start_date(self):
        """After a future transfer, 'home' for a later cover period is the new store."""
        self.login(self.hr)
        self.client.post(
            reverse("employee-transfer", args=[self.employee.pk]),
            {"store": self.store_c.pk, "effective_date": days_from_today(2).isoformat()},
        )
        response = self.request(self.manager_c, store=self.store_b, start=3, end=4)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["home_store"]["id"], self.store_c.id)

    # --- who can see requests, and staff on cover ---

    def test_only_involved_managers_and_the_employee_see_a_request(self):
        created = self.request(self.manager_a)
        other = self.request(self.manager_b, employee=self.other, store=self.store_a)
        self.assertEqual(other.status_code, 201, other.data)

        def ids(user):
            self.login(user)
            return {row["id"] for row in self.client.get(reverse("cover-request-list")).data}

        both = {created.data["id"], other.data["id"]}
        self.assertEqual(ids(self.owner), both)
        self.assertEqual(ids(self.manager_a), both)  # home of one, destination of the other
        self.assertEqual(ids(self.manager_b), both)
        self.assertEqual(ids(self.manager_c), set())
        self.assertEqual(ids(self.employee.user), {created.data["id"]})
        self.assertEqual(ids(self.other.user), {other.data["id"]})

    def test_destination_manager_sees_visiting_staff_only_once_approved(self):
        created = self.request(self.manager_b, store=self.store_b, start=0, end=2)
        self.assertEqual(created.status_code, 201, created.data)

        def visible_to_b():
            self.login(self.manager_b)
            return {row["user"]["email"] for row in self.client.get(reverse("employee-list")).data}

        self.assertNotIn("emp-a@example.com", visible_to_b())
        self.act(self.manager_a, created.data["id"], "approve")
        self.assertIn("emp-a@example.com", visible_to_b())
        self.assertIn("emp-b@example.com", visible_to_b())

    def test_destination_manager_sees_approved_cover_before_it_starts_but_not_after_it_ends(self):
        """So next week's rota can be planned. Pending requests stay hidden; finished ones disappear."""
        self.login(self.manager_a)
        upcoming = self.client.post(reverse("cover-request-list"), {
            "employee": self.employee.pk, "store": self.store_b.pk,
            "valid_from": days_from_today(10).isoformat(), "valid_to": days_from_today(12).isoformat(),
        })
        self.assertEqual(upcoming.status_code, 201, upcoming.data)

        def visible_to_b():
            self.login(self.manager_b)
            return {row["user"]["email"] for row in self.client.get(reverse("employee-list")).data}

        self.assertNotIn("emp-a@example.com", visible_to_b())  # still pending
        self.act(self.manager_b, upcoming.data["id"], "approve")
        self.assertIn("emp-a@example.com", visible_to_b())  # approved, starts in 10 days

        finished = StoreAssignment.objects.get(pk=upcoming.data["id"])
        finished.valid_from, finished.valid_to = days_from_today(-5), days_from_today(-2)
        finished.save()
        self.assertNotIn("emp-a@example.com", visible_to_b())  # cover has ended

    def test_home_store_unchanged_by_cover(self):
        self.request(self.hr)
        self.assertEqual(services.home_store_on(self.employee, days_from_today(4)), self.store_a)
        self.login(self.hr)
        profile = self.client.get(reverse("employee-detail", args=[self.employee.pk]))
        self.assertEqual(profile.data["home_store"]["id"], self.store_a.id)

    def test_cover_requests_cannot_be_deleted_or_edited(self):
        created = self.request(self.hr)
        url = reverse("cover-request-detail", args=[created.data["id"]])
        self.assertEqual(self.client.delete(url).status_code, 405)
        self.assertEqual(self.client.patch(url, {"status": "pending"}).status_code, 405)
