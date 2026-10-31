from django.urls import reverse
from rest_framework.test import APITestCase

from .models import Role, User

STRONG_PASSWORD = "Correct-Horse-9-Battery"


def make_user(email, role, password=STRONG_PASSWORD, **extra):
    return User.objects.create_user(email=email, password=password, role=role, **extra)


class UserModelTests(APITestCase):
    def test_email_is_login_and_lowercased(self):
        user = User.objects.create_user(email="Staff@Example.COM", password=STRONG_PASSWORD)
        self.assertEqual(user.email, "staff@example.com")
        self.assertEqual(user.role, Role.EMPLOYEE)

    def test_superuser_is_owner(self):
        user = User.objects.create_superuser(email="boss@example.com", password=STRONG_PASSWORD)
        self.assertEqual(user.role, Role.OWNER)
        self.assertTrue(user.is_staff and user.is_superuser)


class AuthTests(APITestCase):
    def setUp(self):
        self.user = make_user("emp@example.com", Role.EMPLOYEE, first_name="Sam")

    def login(self, email="emp@example.com", password=STRONG_PASSWORD):
        return self.client.post(reverse("login"), {"email": email, "password": password})

    def test_login_returns_tokens(self):
        response = self.login()
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)

    def test_refresh_has_a_higher_limit_than_login(self):
        from django.core.cache import cache

        cache.clear()
        refresh = self.login().data["refresh"]
        for _ in range(15):  # more than the login limit of 10 a minute
            self.assertEqual(self.client.post(reverse("token-refresh"), {"refresh": refresh}).status_code, 200)
        cache.clear()

    def test_wrong_password_rejected(self):
        self.assertEqual(self.login(password="wrong").status_code, 401)

    def test_inactive_user_cannot_log_in(self):
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.login().status_code, 401)

    def test_me_requires_auth(self):
        self.assertEqual(self.client.get(reverse("me")).status_code, 401)

    def test_me_returns_own_profile_without_password(self):
        token = self.login().data["access"]
        response = self.client.get(reverse("me"), HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["email"], "emp@example.com")
        self.assertNotIn("password", response.data)

    def test_change_password(self):
        self.client.force_authenticate(self.user)
        new = "Another-Strong-Pass-77"
        response = self.client.post(
            reverse("change-password"), {"old_password": STRONG_PASSWORD, "new_password": new}
        )
        self.assertEqual(response.status_code, 204)
        self.client.force_authenticate(None)
        self.assertEqual(self.login(password=new).status_code, 200)

    def test_change_password_rejects_wrong_old_and_weak_new(self):
        self.client.force_authenticate(self.user)
        bad_old = self.client.post(
            reverse("change-password"), {"old_password": "nope", "new_password": "Another-Strong-Pass-77"}
        )
        self.assertEqual(bad_old.status_code, 400)
        weak = self.client.post(
            reverse("change-password"), {"old_password": STRONG_PASSWORD, "new_password": "12345"}
        )
        self.assertEqual(weak.status_code, 400)


class UserManagementTests(APITestCase):
    def setUp(self):
        self.owner = make_user("owner@example.com", Role.OWNER)
        self.hr = make_user("hr@example.com", Role.HR)
        self.manager = make_user("mgr@example.com", Role.MANAGER)
        self.employee = make_user("emp@example.com", Role.EMPLOYEE)

    def new_user_payload(self, **overrides):
        payload = {
            "email": "new@example.com",
            "first_name": "New",
            "last_name": "Starter",
            "role": Role.EMPLOYEE,
            "password": STRONG_PASSWORD,
        }
        payload.update(overrides)
        return payload

    def test_only_owner_and_hr_can_list_users(self):
        for user, expected in [(self.owner, 200), (self.hr, 200), (self.manager, 403), (self.employee, 403)]:
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get(reverse("user-list")).status_code, expected, user.role)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(reverse("user-list")).status_code, 401)

    def test_hr_can_create_employee_and_password_is_hashed(self):
        self.client.force_authenticate(self.hr)
        response = self.client.post(reverse("user-list"), self.new_user_payload())
        self.assertEqual(response.status_code, 201)
        self.assertNotIn("password", response.data)
        created = User.objects.get(email="new@example.com")
        self.assertNotEqual(created.password, STRONG_PASSWORD)
        self.assertTrue(created.check_password(STRONG_PASSWORD))

    def test_manager_cannot_create_users(self):
        self.client.force_authenticate(self.manager)
        response = self.client.post(reverse("user-list"), self.new_user_payload())
        self.assertEqual(response.status_code, 403)

    def test_hr_cannot_create_owner_but_owner_can(self):
        self.client.force_authenticate(self.hr)
        response = self.client.post(reverse("user-list"), self.new_user_payload(role=Role.OWNER))
        self.assertEqual(response.status_code, 400)
        self.client.force_authenticate(self.owner)
        response = self.client.post(reverse("user-list"), self.new_user_payload(role=Role.OWNER))
        self.assertEqual(response.status_code, 201)

    def test_duplicate_email_rejected_case_insensitively(self):
        self.client.force_authenticate(self.hr)
        response = self.client.post(reverse("user-list"), self.new_user_payload(email="EMP@example.com"))
        self.assertEqual(response.status_code, 400)

    def test_weak_password_rejected_on_create(self):
        self.client.force_authenticate(self.hr)
        response = self.client.post(reverse("user-list"), self.new_user_payload(password="password"))
        self.assertEqual(response.status_code, 400)

    def test_hr_cannot_promote_to_owner_or_edit_owner(self):
        self.client.force_authenticate(self.hr)
        promote = self.client.patch(reverse("user-detail", args=[self.employee.pk]), {"role": Role.OWNER})
        self.assertEqual(promote.status_code, 400)
        edit_owner = self.client.patch(reverse("user-detail", args=[self.owner.pk]), {"is_active": False})
        self.assertEqual(edit_owner.status_code, 400)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.is_active)

    def test_hr_can_deactivate_employee(self):
        self.client.force_authenticate(self.hr)
        response = self.client.patch(reverse("user-detail", args=[self.employee.pk]), {"is_active": False})
        self.assertEqual(response.status_code, 200)
        self.employee.refresh_from_db()
        self.assertFalse(self.employee.is_active)

    def test_cannot_deactivate_or_demote_self(self):
        self.client.force_authenticate(self.owner)
        deactivate = self.client.patch(reverse("user-detail", args=[self.owner.pk]), {"is_active": False})
        self.assertEqual(deactivate.status_code, 400)
        demote = self.client.patch(reverse("user-detail", args=[self.owner.pk]), {"role": Role.HR})
        self.assertEqual(demote.status_code, 400)

    def test_email_cannot_be_changed_via_patch(self):
        self.client.force_authenticate(self.hr)
        self.client.patch(reverse("user-detail", args=[self.employee.pk]), {"email": "hijack@example.com"})
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.email, "emp@example.com")

    def test_delete_is_not_allowed(self):
        self.client.force_authenticate(self.owner)
        response = self.client.delete(reverse("user-detail", args=[self.employee.pk]))
        self.assertEqual(response.status_code, 405)


class HealthTests(APITestCase):
    def test_health_is_public(self):
        response = self.client.get(reverse("health"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"status": "ok"})
