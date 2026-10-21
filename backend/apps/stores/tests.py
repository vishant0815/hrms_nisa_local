from django.urls import reverse
from rest_framework.test import APITestCase

from apps.accounts.models import Role
from apps.core.testutils import days_from_today, make_user
from apps.employees import services
from apps.stores.models import Store


class StoreTestBase(APITestCase):
    def setUp(self):
        self.owner = make_user("owner@example.com", Role.OWNER)
        self.hr = make_user("hr@example.com", Role.HR)
        self.manager_a = make_user("mgr-a@example.com", Role.MANAGER)
        self.manager_b = make_user("mgr-b@example.com", Role.MANAGER)
        self.store_a = Store.objects.create(name="Shop A")
        self.store_b = Store.objects.create(name="Shop B")
        self.store_a.managers.add(self.manager_a)
        self.store_b.managers.add(self.manager_b)

    def hire(self, email, store):
        user = make_user(email, Role.EMPLOYEE)
        return services.create_employee(
            user=user, home_store=store, start_date=days_from_today(-30), created_by=self.hr
        )


class StoreManagementTests(StoreTestBase):
    def test_owner_and_hr_can_create_store(self):
        for user in (self.owner, self.hr):
            self.client.force_authenticate(user)
            response = self.client.post(
                reverse("store-list"), {"name": f"New by {user.role}", "radius_m": 150}
            )
            self.assertEqual(response.status_code, 201, user.role)

    def test_manager_and_employee_cannot_create_store(self):
        employee = make_user("emp@example.com")
        for user in (self.manager_a, employee):
            self.client.force_authenticate(user)
            response = self.client.post(reverse("store-list"), {"name": "Nope"})
            self.assertEqual(response.status_code, 403, user.role)

    def test_duplicate_name_rejected(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(reverse("store-list"), {"name": "Shop A"})
        self.assertEqual(response.status_code, 400)

    def test_latitude_and_longitude_must_be_set_together(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(reverse("store-list"), {"name": "Geo", "latitude": "51.5"})
        self.assertEqual(response.status_code, 400)
        ok = self.client.post(
            reverse("store-list"), {"name": "Geo", "latitude": "51.5072", "longitude": "-0.1276"}
        )
        self.assertEqual(ok.status_code, 201)

    def test_out_of_range_coordinates_rejected(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            reverse("store-list"), {"name": "Bad", "latitude": "95", "longitude": "0"}
        )
        self.assertEqual(response.status_code, 400)

    def test_only_manager_role_users_can_be_store_managers(self):
        employee = make_user("emp@example.com", Role.EMPLOYEE)
        self.client.force_authenticate(self.owner)
        response = self.client.patch(
            reverse("store-detail", args=[self.store_a.pk]), {"managers": [employee.pk]}
        )
        self.assertEqual(response.status_code, 400)
        ok = self.client.patch(
            reverse("store-detail", args=[self.store_a.pk]),
            {"managers": [self.manager_a.pk, self.manager_b.pk]},
        )
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(set(self.store_a.managers.all()), {self.manager_a, self.manager_b})

    def test_stores_cannot_be_deleted(self):
        self.client.force_authenticate(self.owner)
        response = self.client.delete(reverse("store-detail", args=[self.store_a.pk]))
        self.assertEqual(response.status_code, 405)

    def test_anonymous_gets_401(self):
        self.assertEqual(self.client.get(reverse("store-list")).status_code, 401)


class StoreVisibilityTests(StoreTestBase):
    def names(self, user):
        self.client.force_authenticate(user)
        response = self.client.get(reverse("store-list"))
        self.assertEqual(response.status_code, 200)
        return {item["name"] for item in response.data}

    def test_owner_and_hr_see_all_stores(self):
        self.assertEqual(self.names(self.owner), {"Shop A", "Shop B"})
        self.assertEqual(self.names(self.hr), {"Shop A", "Shop B"})

    def test_manager_sees_only_their_store(self):
        self.assertEqual(self.names(self.manager_a), {"Shop A"})
        self.assertEqual(self.names(self.manager_b), {"Shop B"})

    def test_manager_cannot_open_another_store(self):
        self.client.force_authenticate(self.manager_a)
        response = self.client.get(reverse("store-detail", args=[self.store_b.pk]))
        self.assertEqual(response.status_code, 404)

    def test_manager_cannot_edit_their_own_store(self):
        self.client.force_authenticate(self.manager_a)
        response = self.client.patch(reverse("store-detail", args=[self.store_a.pk]), {"phone": "123"})
        self.assertEqual(response.status_code, 403)

    def test_employee_sees_only_assigned_store(self):
        employee = self.hire("emp@example.com", self.store_a)
        self.assertEqual(self.names(employee.user), {"Shop A"})

    def test_user_with_no_assignment_sees_nothing(self):
        self.assertEqual(self.names(make_user("loose@example.com")), set())


class StoreHoursTests(StoreTestBase):
    def url(self, store):
        return reverse("store-hours", args=[store.pk])

    def test_owner_can_set_hours_and_manager_can_read_them(self):
        self.client.force_authenticate(self.owner)
        payload = [
            {"weekday": 0, "opens": "07:00", "closes": "23:00"},
            {"weekday": 6, "is_closed": True},
        ]
        response = self.client.put(self.url(self.store_a), payload, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)

        self.client.force_authenticate(self.manager_a)
        read = self.client.get(self.url(self.store_a))
        self.assertEqual(read.status_code, 200)
        self.assertEqual([h["weekday"] for h in read.data], [0, 6])
        self.assertTrue(read.data[1]["is_closed"])
        self.assertIsNone(read.data[1]["opens"])

    def test_manager_cannot_change_hours(self):
        self.client.force_authenticate(self.manager_a)
        response = self.client.put(
            self.url(self.store_a), [{"weekday": 0, "opens": "07:00", "closes": "23:00"}], format="json"
        )
        self.assertEqual(response.status_code, 403)

    def test_manager_cannot_read_another_stores_hours(self):
        self.client.force_authenticate(self.manager_a)
        self.assertEqual(self.client.get(self.url(self.store_b)).status_code, 404)

    def test_put_replaces_existing_hours(self):
        self.client.force_authenticate(self.owner)
        week = [{"weekday": d, "opens": "08:00", "closes": "22:00"} for d in range(7)]
        self.assertEqual(len(self.client.put(self.url(self.store_a), week, format="json").data), 7)
        two = self.client.put(self.url(self.store_a), week[:2], format="json")
        self.assertEqual(len(two.data), 2)

    def test_duplicate_weekday_rejected(self):
        self.client.force_authenticate(self.owner)
        payload = [
            {"weekday": 1, "opens": "08:00", "closes": "22:00"},
            {"weekday": 1, "opens": "09:00", "closes": "21:00"},
        ]
        self.assertEqual(self.client.put(self.url(self.store_a), payload, format="json").status_code, 400)

    def test_open_day_needs_times_and_they_must_differ(self):
        self.client.force_authenticate(self.owner)
        missing = self.client.put(self.url(self.store_a), [{"weekday": 2}], format="json")
        self.assertEqual(missing.status_code, 400)
        same = self.client.put(
            self.url(self.store_a), [{"weekday": 2, "opens": "08:00", "closes": "08:00"}], format="json"
        )
        self.assertEqual(same.status_code, 400)

    def test_overnight_hours_are_allowed(self):
        self.client.force_authenticate(self.owner)
        response = self.client.put(
            self.url(self.store_a), [{"weekday": 4, "opens": "07:00", "closes": "01:00"}], format="json"
        )
        self.assertEqual(response.status_code, 200)

    def test_invalid_replacement_keeps_old_hours(self):
        self.client.force_authenticate(self.owner)
        self.client.put(
            self.url(self.store_a), [{"weekday": 0, "opens": "08:00", "closes": "22:00"}], format="json"
        )
        bad = self.client.put(self.url(self.store_a), [{"weekday": 1}], format="json")
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(len(self.client.get(self.url(self.store_a)).data), 1)
