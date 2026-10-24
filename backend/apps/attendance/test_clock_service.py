"""The FastAPI clock service, driven over HTTP the way a phone would use it."""
from unittest import mock

from fastapi.testclient import TestClient
from rest_framework.test import APITransactionTestCase
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from apps.accounts.models import Role
from apps.core.testutils import make_user
from apps.rota.tests import RotaTestBase

from attendance_api.main import app

from .models import AttendanceRecord, ReviewStatus
from .tests_base import FAR, NEAR, at, attendance_fixture, make_shift


class ClockServiceTests(APITransactionTestCase):
    """Real commits (not a rolled-back transaction) because the service runs in other threads."""

    serialized_rollback = True  # put the seeded visa types back after each test empties the tables

    hire = RotaTestBase.hire
    clear = RotaTestBase.clear

    def setUp(self):
        attendance_fixture(self)

    def shift(self, employee, n, start, end, week=None):
        return make_shift(self, employee, n, start, end, week)

    def api(self, user=None, when="09:00", n=0):
        client = TestClient(app)
        if user is not None:
            client.headers["Authorization"] = f"Bearer {AccessToken.for_user(user)}"
        patcher = mock.patch("apps.attendance.services.current_time", return_value=at(n, when))
        patcher.start()
        self.addCleanup(patcher.stop)
        return client

    def where(self, point=NEAR, **extra):
        return {"latitude": point[0], "longitude": point[1], "accuracy_m": 12.4, "device_id": "phone-1", **extra}

    def test_health_needs_no_login(self):
        self.assertEqual(TestClient(app).get("/health").json(), {"status": "ok"})

    def test_clock_in_then_status_then_clock_out(self):
        client = self.api(self.emp_a.user, "09:02")
        response = client.post("/clock/in", json=self.where())
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual((body["late_minutes"], body["needs_review"], body["store"]["name"]), (2, False, "Shop A"))
        status = client.get("/clock/status").json()
        self.assertTrue(status["clocked_in"])
        out = self.api(self.emp_a.user, "17:00").post("/clock/out", json=self.where())
        self.assertEqual(out.status_code, 200, out.text)
        self.assertFalse(TestClient(app, headers=client.headers).get("/clock/status").json()["clocked_in"])
        self.assertEqual(AttendanceRecord.objects.get().worked_minutes, 478)

    def test_the_phones_own_idea_of_the_time_is_ignored(self):
        client = self.api(self.emp_a.user, "09:00")
        response = client.post("/clock/in", json={**self.where(), "clock_in_at": "2001-01-01T00:00:00Z", "now": "x"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(AttendanceRecord.objects.get().clock_in_at, at(0, "09:00"))

    def test_a_clock_in_away_from_the_shop_is_saved_and_sent_for_review(self):
        response = self.api(self.emp_a.user).post("/clock/in", json=self.where(FAR))
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()["needs_review"])
        self.assertIn("manager will check", response.json()["message"])
        self.assertEqual(AttendanceRecord.objects.get().review_status, ReviewStatus.PENDING)

    def test_refusing_location_is_allowed_but_flagged(self):
        response = self.api(self.emp_a.user).post("/clock/in", json={})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(AttendanceRecord.objects.get().flags, ["no_location"])

    def test_rule_failures_come_back_as_clear_codes(self):
        client = self.api(self.emp_a.user, "09:00", n=3)
        self.assertEqual((client.post("/clock/in", json={}).status_code, client.post("/clock/in", json={}).json()["code"]), (409, "no_shift"))
        early = self.api(self.emp_a.user, "07:00").post("/clock/in", json={})
        self.assertEqual((early.status_code, early.json()["code"]), (409, "too_early"))
        not_in = self.api(self.emp_a.user).post("/clock/out", json={})
        self.assertEqual((not_in.status_code, not_in.json()["code"]), (409, "not_clocked_in"))
        client = self.api(self.emp_a.user)
        client.post("/clock/in", json={})
        again = client.post("/clock/in", json={})
        self.assertEqual((again.status_code, again.json()["code"]), (409, "already_clocked_in"))
        self.assertEqual(AttendanceRecord.objects.count(), 1)

    def test_bad_payloads_are_rejected_before_any_rule_runs(self):
        client = self.api(self.emp_a.user)
        for payload in (
            {"latitude": 100, "longitude": 0},
            {"latitude": 51.5},
            {"latitude": 51.5, "longitude": -0.1, "accuracy_m": -5},
            {"device_id": "has spaces and <tags>"},
            {"device_id": "x" * 65},
        ):
            with self.subTest(payload=payload):
                self.assertEqual(client.post("/clock/in", json=payload).status_code, 422)
        self.assertEqual(AttendanceRecord.objects.count(), 0)

    def test_login_is_required_and_only_real_access_tokens_work(self):
        anonymous = self.api()
        self.assertEqual(anonymous.post("/clock/in", json={}).status_code, 401)
        self.assertEqual(anonymous.get("/clock/status").status_code, 401)
        for bad in ("garbage", str(RefreshToken.for_user(self.emp_a.user)), ""):
            with self.subTest(token=bad[:10]):
                client = TestClient(app, headers={"Authorization": f"Bearer {bad}"})
                self.assertEqual(client.post("/clock/in", json={}).status_code, 401)
        self.assertEqual(AttendanceRecord.objects.count(), 0)

    def test_deactivated_users_and_people_without_a_profile_are_turned_away(self):
        token = AccessToken.for_user(self.emp_a.user)
        self.emp_a.user.is_active = False
        self.emp_a.user.save()
        client = TestClient(app, headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(client.post("/clock/in", json={}).status_code, 401)
        self.assertEqual(self.api(self.manager_a).post("/clock/in", json={}).status_code, 403)
        self.assertEqual(AttendanceRecord.objects.count(), 0)

    def test_employees_clock_only_themselves(self):
        self.api(self.emp_b.user).post("/clock/in", json={})
        self.assertEqual(AttendanceRecord.objects.count(), 0)  # emp_b has no shift now; nothing was created for emp_a
