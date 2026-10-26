"""The device endpoints of the FastAPI clock service, driven over HTTP the way a device would."""
from unittest import mock

from fastapi.testclient import TestClient
from rest_framework.test import APITransactionTestCase
from rest_framework_simplejwt.tokens import AccessToken

from apps.attendance.models import AttendanceRecord
from apps.attendance.tests_base import at, attendance_fixture, make_shift
from apps.rota.tests import RotaTestBase

from attendance_api.main import app

from . import services
from .models import ClockDevice, Punch


class DeviceServiceTests(APITransactionTestCase):
    """Real commits, because the service runs in other threads."""

    serialized_rollback = True

    hire = RotaTestBase.hire
    clear = RotaTestBase.clear

    def setUp(self):
        attendance_fixture(self)
        self.device, self.secret = services.register_device(store=self.store_a, name="Front door", serial="ZK-0001")
        self.enrolment = services.enrol(employee=self.emp_a, by=self.hr, consent_note="Signed form")

    def api(self, when="09:00", secret=None, serial="ZK-0001"):
        patcher = mock.patch("apps.attendance.services.current_time", return_value=at(0, when))
        patcher.start()
        self.addCleanup(patcher.stop)
        headers = {"X-Device-Serial": serial}
        if secret is not False:
            headers["Authorization"] = f"Bearer {secret or self.secret}"
        return TestClient(app, headers=headers)

    def body(self, kind="in", when="09:00", user=1):
        return {"user_id": user, "kind": kind, "time": at(0, when).isoformat()}

    def test_a_punch_makes_a_record(self):
        response = self.api().post("/clock/device/punch", json=self.body())
        self.assertEqual((response.status_code, response.json()["outcome"], response.json()["reason"]), (200, "accepted", ""))
        record = AttendanceRecord.objects.get()
        self.assertEqual((record.employee, record.source, record.store), (self.emp_a, "fingerprint", self.store_a))

    def test_a_refused_punch_still_answers_200_with_the_reason(self):
        response = self.api().post("/clock/device/punch", json=self.body(user=42))
        self.assertEqual((response.status_code, response.json()["outcome"], response.json()["reason"]), (200, "rejected", "unknown_user"))

    def test_the_device_must_prove_who_it_is(self):
        cases = [
            self.api(secret=False),                # no secret at all
            self.api(secret="not-the-secret"),
            self.api(serial="ZK-9999"),
            self.api(serial=""),
        ]
        for client in cases:
            self.assertEqual(client.post("/clock/device/punch", json=self.body()).status_code, 401)
        self.assertEqual(AttendanceRecord.objects.count(), 0)
        ClockDevice.objects.update(is_active=False)
        self.assertEqual(self.api().post("/clock/device/punch", json=self.body()).status_code, 401)

    def test_a_staff_login_token_does_not_work_as_a_device(self):
        client = TestClient(app, headers={"Authorization": f"Bearer {AccessToken.for_user(self.hr)}", "X-Device-Serial": "ZK-0001"})
        self.assertEqual(client.post("/clock/device/punch", json=self.body()).status_code, 401)

    def test_a_device_secret_does_not_work_for_the_phone_routes(self):
        client = self.api()
        self.assertEqual(client.post("/clock/in", json={}).status_code, 401)

    def test_bad_bodies_are_refused_before_anything_happens(self):
        client = self.api()
        for body in [
            {**self.body(), "kind": "lunch"},
            {**self.body(), "time": "2026-11-02T09:00:00"},  # no timezone
            {**self.body(), "user_id": -1},
            {"kind": "in", "time": self.body()["time"]},
        ]:
            self.assertEqual(client.post("/clock/device/punch", json=body).status_code, 422, body)
        self.assertEqual(Punch.objects.count(), 0)

    def test_a_batch_held_offline_is_handled_oldest_first(self):
        client = self.api("17:05")
        batch = {"punches": [self.body("out", "17:00"), self.body("break_end", "12:20"), self.body("in", "09:00"), self.body("break_start", "12:00")]}
        response = client.post("/clock/device/punches", json=batch)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([r["outcome"] for r in response.json()["results"]], ["accepted"] * 4)
        record = AttendanceRecord.objects.get()
        self.assertEqual((record.clock_in_at, record.clock_out_at, record.breaks.count()), (at(0, "09:00"), at(0, "17:05"), 1))  # the out punch was within 5 minutes: our clock
        self.assertIn("delayed_punch", record.flags)  # the others arrived long after they happened

    def test_a_batch_has_a_size_limit(self):
        batch = {"punches": [self.body()] * 101}
        self.assertEqual(self.api().post("/clock/device/punches", json=batch).status_code, 422)
        self.assertEqual(self.api().post("/clock/device/punches", json={"punches": []}).status_code, 422)

    def test_the_heartbeat_records_that_the_device_is_alive_and_how_far_out_its_clock_is(self):
        response = self.api("09:00").post("/clock/device/heartbeat", json={"time": at(0, "08:58").isoformat()})
        self.assertEqual(response.status_code, 200)
        self.device.refresh_from_db()
        self.assertEqual((self.device.last_seen_at, self.device.clock_skew_seconds), (at(0, "09:00"), 120))

    def test_repeating_a_request_is_safe(self):
        client = self.api()
        first = client.post("/clock/device/punch", json=self.body()).json()
        again = client.post("/clock/device/punch", json=self.body()).json()
        self.assertEqual(first, again)
        self.assertEqual(AttendanceRecord.objects.count(), 1)
