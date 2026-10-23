import json
from unittest import mock

from django.urls import reverse

from apps.rota import services as rota_services

from .models import AttendanceRecord, ReviewStatus
from .tests_base import FAR, AttendanceBase, at, day


class ApiBase(AttendanceBase):
    def setUp(self):
        super().setUp()
        self.shift_b = self.shift(self.emp_b, 0, "09:00", "17:00", week=self.week_b)
        rota_services.publish_week(rota_week=self.week_b, by=self.hr)
        self.clean = self.clock_in("09:00")  # emp_a, no problems
        self.clock_out("17:00")
        self.flagged = self.clock_in("09:00", where=FAR, employee=self.emp_b)  # emp_b at Shop B
        # employee b's shop has no location set up, so force a flag the way a real check would
        AttendanceRecord.objects.filter(pk=self.flagged.pk).update(flags=["out_of_area"], review_status=ReviewStatus.PENDING)
        self.flagged.refresh_from_db()
        # The rota is next week, so make the service believe it is the day after the shifts.
        patcher = mock.patch("apps.attendance.services.current_time", return_value=at(1, "12:00"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def get(self, user, name, *args, **params):
        self.login(user)
        return self.client.get(reverse(name, args=args), params)

    def post(self, user, name, *args, **body):
        self.login(user)
        return self.client.post(reverse(name, args=args), body, format="json")

    def patch(self, user, name, *args, **body):
        self.login(user)
        return self.client.patch(reverse(name, args=args), body, format="json")

    def window(self):
        return {"start": day(0).isoformat(), "end": day(0).isoformat()}


class ManagerListTests(ApiBase):
    def test_a_manager_sees_only_their_own_stores_records(self):
        ids = lambda response: [r["id"] for r in response.data]  # noqa: E731
        self.assertEqual(ids(self.get(self.manager_a, "attendance-list", **self.window())), [self.clean.pk])
        self.assertEqual(ids(self.get(self.manager_b, "attendance-list", **self.window())), [self.flagged.pk])
        self.assertEqual(sorted(ids(self.get(self.owner, "attendance-list", **self.window()))), sorted([self.clean.pk, self.flagged.pk]))
        self.assertEqual(len(self.get(self.hr, "attendance-list", **self.window()).data), 2)

    def test_employees_cannot_use_the_manager_endpoints(self):
        for name, args in (("attendance-list", ()), ("attendance-detail", (self.clean.pk,)), ("attendance-edits", (self.clean.pk,))):
            self.assertEqual(self.get(self.emp_a.user, name, *args).status_code, 403)
        self.assertEqual(self.post(self.emp_a.user, "attendance-approve", self.flagged.pk).status_code, 403)

    def test_filters(self):
        pending = self.get(self.owner, "attendance-list", review="pending", **self.window())
        self.assertEqual([r["id"] for r in pending.data], [self.flagged.pk])
        flagged = self.get(self.owner, "attendance-list", review="flagged", **self.window())
        self.assertEqual([r["id"] for r in flagged.data], [self.flagged.pk])
        by_store = self.get(self.owner, "attendance-list", store=self.store_a.pk, **self.window())
        self.assertEqual([r["id"] for r in by_store.data], [self.clean.pk])
        by_person = self.get(self.owner, "attendance-list", employee=self.emp_b.pk, **self.window())
        self.assertEqual([r["id"] for r in by_person.data], [self.flagged.pk])
        self.assertEqual(self.get(self.owner, "attendance-list", review="nonsense").status_code, 400)
        self.assertEqual(self.get(self.owner, "attendance-list", start=day(3).isoformat(), end=day(4).isoformat()).data, [])

    def test_output_has_distances_and_plain_flags_but_never_raw_coordinates(self):
        item = self.get(self.manager_b, "attendance-list", **self.window()).data[0]
        self.assertEqual(item["flags"], [{"code": "out_of_area", "message": "Clocked away from the shop."}])
        self.assertIn("in_distance_m", item)
        body = json.dumps(self.get(self.owner, "attendance-list", **self.window()).data)
        for secret in ("51.50", "-0.12", "latitude", "longitude", "device_id", "phone"):
            self.assertNotIn(secret, body)


class ManagerActionTests(ApiBase):
    def test_approve_and_reject_through_the_api(self):
        approved = self.post(self.manager_b, "attendance-approve", self.flagged.pk, note="Was at the cash and carry")
        self.assertEqual(approved.status_code, 200, approved.data)
        self.assertEqual(approved.data["review_status"], "approved")
        self.assertEqual(self.post(self.manager_b, "attendance-reject", self.flagged.pk, note="x").status_code, 400)

    def test_rejecting_needs_a_reason(self):
        self.assertEqual(self.post(self.manager_b, "attendance-reject", self.flagged.pk).status_code, 400)
        self.assertEqual(self.post(self.manager_b, "attendance-reject", self.flagged.pk, note="Not there").status_code, 200)

    def test_another_stores_manager_gets_a_404_for_everything(self):
        for name in ("attendance-approve", "attendance-reject"):
            self.assertEqual(self.post(self.manager_a, name, self.flagged.pk, note="x").status_code, 404)
        self.assertEqual(self.get(self.manager_a, "attendance-detail", self.flagged.pk).status_code, 404)
        self.assertEqual(self.patch(self.manager_a, "attendance-detail", self.flagged.pk, reason="x", clock_in_at=at(0, "09:10").isoformat()).status_code, 404)
        self.assertEqual(self.get(self.manager_a, "attendance-edits", self.flagged.pk).status_code, 404)

    def test_editing_needs_a_reason_and_keeps_a_history(self):
        url = ("attendance-detail", self.clean.pk)
        self.assertEqual(self.patch(self.manager_a, *url, clock_out_at=at(0, "17:30").isoformat()).status_code, 400)
        done = self.patch(self.manager_a, *url, clock_out_at=at(0, "17:30").isoformat(), reason="Stayed to close")
        self.assertEqual(done.status_code, 200, done.data)
        self.assertEqual(done.data["worked_minutes"], 510)
        self.assertIn("manually_edited", [f["code"] for f in done.data["flags"]])
        history = self.get(self.manager_a, "attendance-edits", self.clean.pk).data
        self.assertEqual([(h["reason"], h["edited_by"]) for h in history], [("Stayed to close", "mgr-a@example.com")])
        self.assertIn("clock_out_at", history[0]["changes"])

    def test_manual_records_respect_who_runs_the_store(self):
        body = dict(
            employee=self.emp_a.pk, store=self.store_a.pk, clock_in_at=at(1, "09:00").isoformat(),
            clock_out_at=at(1, "11:00").isoformat(), reason="Forgot to clock",
        )
        self.assertEqual(self.post(self.manager_b, "attendance-manual", **body).status_code, 403)  # not their store
        self.assertEqual(self.post(self.emp_a.user, "attendance-manual", **body).status_code, 403)
        self.assertEqual(self.post(self.manager_a, "attendance-manual", **{**body, "reason": ""}).status_code, 400)
        long_ago = {"clock_in_at": "2020-01-06T09:00:00Z", "clock_out_at": "2020-01-06T17:00:00Z"}
        self.assertEqual(self.post(self.manager_a, "attendance-manual", **{**body, **long_ago}).status_code, 400)
        created = self.post(self.manager_a, "attendance-manual", **body)
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual((created.data["source"], created.data["worked_minutes"]), ("manual", 120))
        history = self.get(self.manager_a, "attendance-edits", created.data["id"]).data
        self.assertEqual(history[0]["reason"], "Forgot to clock")


class MyAttendanceTests(ApiBase):
    def test_employees_see_only_their_own_time_with_a_simple_status(self):
        mine = self.get(self.emp_a.user, "my-attendance", **self.window())
        self.assertEqual([r["id"] for r in mine.data], [self.clean.pk])
        self.assertEqual(mine.data[0]["status"], "ok")
        theirs = self.get(self.emp_b.user, "my-attendance", **self.window()).data[0]
        self.assertEqual(theirs["status"], "clocked_in")

    def test_employees_never_see_flags_locations_or_devices(self):
        AttendanceRecord.objects.filter(pk=self.clean.pk).update(
            flags=["out_of_area", "shared_device"], review_status=ReviewStatus.PENDING, in_device_id="phone-9",
        )
        body = self.get(self.emp_a.user, "my-attendance", **self.window())
        self.assertEqual(body.data[0]["status"], "under_review")
        text = json.dumps(body.data)
        for secret in ("flags", "out_of_area", "shared_device", "distance", "latitude", "phone-9", "accuracy"):
            self.assertNotIn(secret, text)

    def test_rejected_entries_show_the_managers_reason(self):
        self.post(self.manager_b, "attendance-reject", self.flagged.pk, note="Not at the shop")
        mine = self.get(self.emp_b.user, "my-attendance", **self.window()).data[0]
        self.assertEqual((mine["status"], mine["review_note"]), ("clocked_in", "Not at the shop"))

    def test_login_and_profile_are_required(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(reverse("my-attendance")).status_code, 401)
        self.assertEqual(self.get(self.manager_a, "my-attendance").status_code, 404)
