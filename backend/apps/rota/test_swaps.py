import json
from datetime import datetime, time
from unittest import mock

from django.db import IntegrityError, transaction
from django.urls import reverse

from apps.accounts.models import Role
from apps.core.testutils import make_user
from apps.visa import services as visa_services

from . import services, swaps
from .models import Shift, ShiftStatus, ShiftSwap, SwapStatus
from .tests import RotaTestBase, day


def t(text):
    return time.fromisoformat(text)


class SwapBase(RotaTestBase):
    def setUp(self):
        super().setUp()
        self.emp_c = self.hire("emp-c@example.com", self.store_a)
        self.visa_c = self.clear(self.emp_c)
        self.s_a0 = self.shift(self.emp_a, 0)
        self.s_a1 = self.shift(self.emp_a, 1)
        self.s_c2 = self.shift(self.emp_c, 2, notes="private manager note")
        self.s_c3 = self.shift(self.emp_c, 3)
        self.s_b0 = self.shift(self.emp_b, 0, week=self.week_b)
        services.publish_week(rota_week=self.week_a, by=self.hr)
        services.publish_week(rota_week=self.week_b, by=self.hr)

    def shift(self, employee, n, start="09:00", end="17:00", week=None, **extra):
        created, _ = services.create_shift(
            rota_week=week or self.week_a, by=self.hr, employee=employee, date=day(n),
            start_time=t(start), end_time=t(end), **extra,
        )
        return created

    # --- API helpers ---

    def request(self, user, shift, colleague, **extra):
        self.login(user)
        return self.client.post(
            reverse("my-swaps"), {"offered_shift": shift.pk, "colleague": colleague.pk, **extra}, format="json"
        )

    def act(self, user, name, swap_id, **body):
        self.login(user)
        return self.client.post(reverse(f"my-swap-{name}", args=[swap_id]), body, format="json")

    def decide(self, user, name, swap_id, **body):
        self.login(user)
        return self.client.post(reverse(f"swap-{name}", args=[swap_id]), body, format="json")

    def waiting_for_manager(self, offered=None, colleague=None, in_return=None):
        """A swap that has been requested and accepted, so it waits for a manager."""
        offered = offered or self.s_a0
        colleague = colleague or self.emp_c
        swap_id = self.request(self.emp_a.user, offered, colleague).data["id"]
        body = {"in_return_shift": in_return.pk} if in_return else {}
        response = self.act(colleague.user, "accept", swap_id, **body)
        self.assertEqual(response.status_code, 200, response.data)
        return ShiftSwap.objects.get(pk=swap_id)

    def owner_of(self, shift):
        return Shift.objects.get(pk=shift.pk).employee


class RequestTests(SwapBase):
    def test_employee_offers_a_shift_to_a_colleague(self):
        response = self.request(self.emp_a.user, self.s_a0, self.emp_c, note="Dentist")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], "pending_colleague")
        self.assertEqual(response.data["kind"], "hand_over")
        self.assertEqual(response.data["direction"], "outgoing")
        self.assertEqual(self.owner_of(self.s_a0), self.emp_a)  # nothing changes yet

    def test_only_your_own_published_shift_can_be_offered(self):
        other = self.request(self.emp_a.user, self.s_c2, self.emp_c)
        self.assertEqual(self.codes(other), ["shift_not_swappable"])
        week = services.create_week(store=self.store_a, week_start=day(7), by=self.hr)[0]
        draft = services.create_shift(
            rota_week=week, by=self.hr, employee=self.emp_a, date=day(8), start_time=t("09:00"), end_time=t("17:00"),
        )[0]
        self.assertEqual(self.codes(self.request(self.emp_a.user, draft, self.emp_c)), ["shift_not_swappable"])
        self.s_a0.status = ShiftStatus.CANCELLED
        self.s_a0.save()
        self.assertEqual(self.codes(self.request(self.emp_a.user, self.s_a0, self.emp_c)), ["shift_not_swappable"])

    def test_a_shift_that_has_started_cannot_be_offered(self):
        with mock.patch.object(swaps, "_now", return_value=datetime.combine(day(0), t("09:30"))):
            response = self.request(self.emp_a.user, self.s_a0, self.emp_c)
        self.assertEqual(self.codes(response), ["shift_not_swappable"])

    def test_colleague_must_work_at_the_same_store_that_day(self):
        self.assertEqual(self.codes(self.request(self.emp_a.user, self.s_a0, self.emp_b)), ["colleague_not_available"])
        self.assertEqual(self.codes(self.request(self.emp_a.user, self.s_a0, self.emp_a)), ["colleague_not_available"])
        self.assertEqual(self.request(self.emp_a.user, self.s_a0, self.emp_c).status_code, 201)

    def test_approved_cover_makes_someone_from_another_store_eligible_for_those_days_only(self):
        outside = self.shift(self.emp_a, 3, start="10:00", end="14:00")
        self.assertEqual(self.codes(self.request(self.emp_a.user, self.s_a1, self.emp_b)), ["colleague_not_available"])
        self.cover(self.emp_b, self.store_a, first=0, last=1)  # HR asked, so both sides are approved
        self.assertEqual(self.request(self.emp_a.user, self.s_a1, self.emp_b).status_code, 201)
        self.assertEqual(self.codes(self.request(self.emp_a.user, outside, self.emp_b)), ["colleague_not_available"])

    def test_one_open_request_per_shift(self):
        self.assertEqual(self.request(self.emp_a.user, self.s_a0, self.emp_c).status_code, 201)
        self.assertEqual(self.codes(self.request(self.emp_a.user, self.s_a0, self.emp_c)), ["already_requested"])

    def test_the_database_also_refuses_two_open_requests_for_one_shift(self):
        ShiftSwap.objects.create(offered_shift=self.s_a0, requester=self.emp_a, colleague=self.emp_c)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ShiftSwap.objects.create(offered_shift=self.s_a0, requester=self.emp_a, colleague=self.emp_c)

    def test_limit_on_waiting_requests(self):
        for n in range(swaps.MAX_OPEN_REQUESTS):
            ShiftSwap.objects.create(
                offered_shift=self.shift(self.emp_a, 5, start=f"{6 + n:02d}:00", end=f"{6 + n:02d}:30"),
                requester=self.emp_a, colleague=self.emp_c,
            )
        self.assertEqual(self.codes(self.request(self.emp_a.user, self.s_a0, self.emp_c)), ["too_many_requests"])

    def test_a_swap_that_breaks_a_rule_is_refused_without_saying_why(self):
        visa_services.revoke_visa(visa=self.visa_c, by=self.hr, reason="Home Office notice")
        response = self.request(self.emp_a.user, self.s_a0, self.emp_c)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.codes(response), ["swap_not_possible"])
        body = json.dumps(response.data).lower()
        for leak in ("cleared", "visa", "hours", "limit", "hr"):
            self.assertNotIn(leak, body.replace("swap_not_possible", ""))

    def test_overlapping_shift_for_the_colleague_is_refused(self):
        self.shift(self.emp_c, 0, start="12:00", end="18:00")
        self.assertEqual(self.codes(self.request(self.emp_a.user, self.s_a0, self.emp_c)), ["swap_not_possible"])

    def test_login_and_profile_are_required(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(reverse("my-swaps")).status_code, 401)
        self.assertEqual(self.request(self.manager_a, self.s_a0, self.emp_c).status_code, 404)  # no employee profile

    def test_colleague_list_has_names_only_and_the_right_people(self):
        self.login(self.emp_a.user)
        response = self.client.get(reverse("my-swap-colleagues"), {"shift": self.s_a0.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([p["id"] for p in response.data], [self.emp_c.pk])
        self.assertEqual(set(response.data[0]), {"id", "name"})
        self.assertNotIn("@", json.dumps(response.data))
        self.assertEqual(self.client.get(reverse("my-swap-colleagues"), {"shift": self.s_c2.pk}).status_code, 404)
        self.assertEqual(self.client.get(reverse("my-swap-colleagues"), {"shift": "x"}).status_code, 400)


class ColleagueAnswerTests(SwapBase):
    def test_colleague_accepts_a_hand_over(self):
        swap_id = self.request(self.emp_a.user, self.s_a0, self.emp_c).data["id"]
        response = self.act(self.emp_c.user, "accept", swap_id)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "pending_manager")
        self.assertEqual(response.data["direction"], "incoming")
        self.assertEqual(self.owner_of(self.s_a0), self.emp_a)  # still waiting for the manager

    def test_colleague_accepts_and_gives_a_shift_back(self):
        swap = self.waiting_for_manager(in_return=self.s_c2)
        self.assertEqual(swap.requested_shift, self.s_c2)

    def test_the_shift_given_back_must_be_theirs_and_at_the_same_store(self):
        swap_id = self.request(self.emp_a.user, self.s_a0, self.emp_c).data["id"]
        for wrong in (self.s_a1, self.s_b0):
            with self.subTest(shift=wrong.pk):
                response = self.act(self.emp_c.user, "accept", swap_id, in_return_shift=wrong.pk)
                self.assertEqual(self.codes(response), ["shift_not_swappable"])
        self.assertEqual(ShiftSwap.objects.get(pk=swap_id).status, SwapStatus.PENDING_COLLEAGUE)

    def test_a_shift_the_colleague_owns_at_another_store_cannot_be_given_back(self):
        self.cover(self.emp_c, self.store_b, first=0, last=6)
        elsewhere = self.shift(self.emp_c, 5, week=self.week_b)
        swap_id = self.request(self.emp_a.user, self.s_a0, self.emp_c).data["id"]
        response = self.act(self.emp_c.user, "accept", swap_id, in_return_shift=elsewhere.pk)
        self.assertEqual(self.codes(response), ["shift_not_swappable"])

    def test_the_shift_given_back_cannot_already_be_in_another_swap(self):
        first = self.request(self.emp_a.user, self.s_a0, self.emp_c).data["id"]
        second = self.request(self.emp_a.user, self.s_a1, self.emp_c).data["id"]
        self.assertEqual(self.act(self.emp_c.user, "accept", first, in_return_shift=self.s_c2.pk).status_code, 200)
        response = self.act(self.emp_c.user, "accept", second, in_return_shift=self.s_c2.pk)
        self.assertEqual(self.codes(response), ["already_requested"])

    def test_colleague_declines(self):
        swap_id = self.request(self.emp_a.user, self.s_a0, self.emp_c).data["id"]
        response = self.act(self.emp_c.user, "decline", swap_id)
        self.assertEqual(response.data["status"], "declined")
        self.assertEqual(self.codes(self.act(self.emp_c.user, "accept", swap_id)), ["swap_not_waiting"])

    def test_only_the_colleague_can_answer_and_strangers_cannot_even_see_it(self):
        swap_id = self.request(self.emp_a.user, self.s_a0, self.emp_c).data["id"]
        for name in ("accept", "decline"):
            self.assertEqual(self.codes(self.act(self.emp_a.user, name, swap_id)), ["swap_not_waiting"])
        stranger = self.hire("stranger@example.com", self.store_a)
        for name in ("accept", "decline", "cancel"):
            self.assertEqual(self.act(stranger.user, name, swap_id).status_code, 404)
        self.login(stranger.user)
        self.assertEqual(self.client.get(reverse("my-swaps")).data, [])

    def test_if_the_shift_changed_before_the_answer_the_colleague_cannot_accept(self):
        swap_id = self.request(self.emp_a.user, self.s_a0, self.emp_c).data["id"]
        self.login(self.manager_a)
        self.client.post(reverse("shift-cancel", args=[self.s_a0.pk]))
        self.assertEqual(self.codes(self.act(self.emp_c.user, "accept", swap_id)), ["swap_out_of_date"])

    def test_accepting_re_checks_the_rules(self):
        swap_id = self.request(self.emp_a.user, self.s_a0, self.emp_c).data["id"]
        self.shift(self.emp_c, 0, start="10:00", end="12:00")  # now clashes
        self.assertEqual(self.codes(self.act(self.emp_c.user, "accept", swap_id)), ["swap_not_possible"])

    def test_requester_cancels_while_open_but_not_after_approval(self):
        swap_id = self.request(self.emp_a.user, self.s_a0, self.emp_c).data["id"]
        self.assertEqual(self.codes(self.act(self.emp_c.user, "cancel", swap_id)), ["swap_not_open"])
        self.assertEqual(self.act(self.emp_a.user, "cancel", swap_id).data["status"], "cancelled")
        self.assertEqual(self.request(self.emp_a.user, self.s_a0, self.emp_c).status_code, 201)  # can ask again
        swap = self.waiting_for_manager(offered=self.s_a1)
        self.decide(self.manager_a, "approve", swap.pk)
        self.assertEqual(self.codes(self.act(self.emp_a.user, "cancel", swap.pk)), ["swap_not_open"])


class ManagerApprovalTests(SwapBase):
    def test_approval_hands_the_shift_over(self):
        swap = self.waiting_for_manager()
        response = self.decide(self.manager_a, "approve", swap.pk)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "approved")
        self.assertEqual(self.owner_of(self.s_a0), self.emp_c)
        swap.refresh_from_db()
        self.assertEqual(swap.reviewed_by, self.manager_a)
        self.assertIsNotNone(swap.reviewed_at)

    def test_approval_swaps_both_shifts(self):
        swap = self.waiting_for_manager(in_return=self.s_c2)
        self.decide(self.manager_a, "approve", swap.pk)
        self.assertEqual(self.owner_of(self.s_a0), self.emp_c)
        self.assertEqual(self.owner_of(self.s_c2), self.emp_a)

    def test_new_owner_sees_it_in_their_rota_and_the_old_one_does_not(self):
        swap = self.waiting_for_manager()
        self.decide(self.manager_a, "approve", swap.pk)
        self.login(self.emp_c.user)
        self.assertIn(self.s_a0.pk, [s["id"] for s in self.client.get(reverse("my-rota"), {"from": day(0).isoformat(), "to": day(6).isoformat()}).data])
        self.login(self.emp_a.user)
        self.assertNotIn(self.s_a0.pk, [s["id"] for s in self.client.get(reverse("my-rota"), {"from": day(0).isoformat(), "to": day(6).isoformat()}).data])

    def test_owner_and_hr_can_approve_too(self):
        for user, shift in ((self.owner, self.s_a0), (self.hr, self.s_a1)):
            swap = self.waiting_for_manager(offered=shift)
            self.assertEqual(self.decide(user, "approve", swap.pk).status_code, 200)

    def test_another_stores_manager_and_employees_cannot_decide(self):
        swap = self.waiting_for_manager()
        self.assertEqual(self.decide(self.manager_b, "approve", swap.pk).status_code, 404)
        self.assertEqual(self.decide(self.manager_b, "reject", swap.pk).status_code, 404)
        for user in (self.emp_a.user, self.emp_c.user):
            self.assertEqual(self.decide(user, "approve", swap.pk).status_code, 403)
            self.assertEqual(self.decide(user, "reject", swap.pk).status_code, 403)
        self.assertEqual(ShiftSwap.objects.get(pk=swap.pk).status, SwapStatus.PENDING_MANAGER)

    def test_cannot_approve_before_the_colleague_agrees_or_twice(self):
        swap_id = self.request(self.emp_a.user, self.s_a0, self.emp_c).data["id"]
        self.assertEqual(self.codes(self.decide(self.manager_a, "approve", swap_id)), ["swap_not_waiting"])
        self.act(self.emp_c.user, "accept", swap_id)
        self.assertEqual(self.decide(self.manager_a, "approve", swap_id).status_code, 200)
        self.assertEqual(self.codes(self.decide(self.manager_a, "approve", swap_id)), ["swap_not_waiting"])

    def test_approval_re_checks_every_rule_and_changes_nothing_when_blocked(self):
        swap = self.waiting_for_manager(in_return=self.s_c2)
        visa_services.revoke_visa(visa=self.visa_c, by=self.hr, reason="x")
        response = self.decide(self.manager_a, "approve", swap.pk)
        self.assertEqual(response.status_code, 400)
        self.assertIn("not_cleared", self.codes(response))
        self.assertEqual(self.owner_of(self.s_a0), self.emp_a)
        self.assertEqual(self.owner_of(self.s_c2), self.emp_c)
        self.assertEqual(ShiftSwap.objects.get(pk=swap.pk).status, SwapStatus.PENDING_MANAGER)

    def test_double_booking_created_after_the_answer_blocks_approval(self):
        swap = self.waiting_for_manager()
        self.shift(self.emp_c, 0, start="10:00", end="12:00")
        response = self.decide(self.manager_a, "approve", swap.pk)
        self.assertEqual(self.codes(response), ["double_booked"])

    def test_student_hours_cap_is_checked_again_at_approval(self):
        student = self.make_student("s1@example.com")
        mine = self.shift(student, 0, start="09:00", end="17:00")  # 8 hours
        self.shift(student, 1, start="09:00", end="13:00")  # 4 hours
        long_shift = self.shift(self.emp_c, 4, start="08:00", end="20:00")  # 12 hours
        swap_id = self.request(student.user, mine, self.emp_c).data["id"]
        # After the swap the student would work 4 + 12 = 16 hours: fine, so the colleague can accept.
        self.assertEqual(self.act(self.emp_c.user, "accept", swap_id, in_return_shift=long_shift.pk).status_code, 200)
        # Then the manager adds more hours for the student, which is allowed today (8 + 4 + 8 = 20)...
        self.shift(student, 2, start="09:00", end="17:00")
        # ...but not after the swap (4 + 8 + 12 = 24 is over the cap of 20).
        response = self.decide(self.manager_a, "approve", swap_id)
        self.assertEqual(response.status_code, 400)
        self.assertIn("hours_limit", self.codes(response))
        self.assertEqual(self.owner_of(mine), student)
        self.assertEqual(self.owner_of(long_shift), self.emp_c)

    def test_a_swap_that_would_put_a_student_over_the_cap_is_refused_when_accepting(self):
        student = self.make_student("s2@example.com")
        mine = self.shift(student, 0, start="09:00", end="17:00")
        self.shift(student, 1, start="09:00", end="17:00")
        long_shift = self.shift(self.emp_c, 4, start="08:00", end="21:00")  # 13 hours: 8 + 13 = 21
        swap_id = self.request(student.user, mine, self.emp_c).data["id"]
        response = self.act(self.emp_c.user, "accept", swap_id, in_return_shift=long_shift.pk)
        self.assertEqual(self.codes(response), ["swap_not_possible"])

    def test_a_shift_changed_by_the_manager_makes_the_request_out_of_date(self):
        swap = self.waiting_for_manager()
        self.login(self.manager_a)
        self.client.post(reverse("shift-cancel", args=[self.s_a0.pk]))
        self.assertEqual(self.codes(self.decide(self.manager_a, "approve", swap.pk)), ["swap_out_of_date"])
        self.assertEqual(self.decide(self.manager_a, "reject", swap.pk, reason="Shift was cancelled").status_code, 200)

    def test_reject_with_a_reason_the_employees_can_see(self):
        swap = self.waiting_for_manager()
        response = self.decide(self.manager_a, "reject", swap.pk, reason="We are short that day")
        self.assertEqual(response.data["status"], "rejected")
        self.assertEqual(self.owner_of(self.s_a0), self.emp_a)
        for user in (self.emp_a.user, self.emp_c.user):
            self.login(user)
            mine = self.client.get(reverse("my-swaps")).data[0]
            self.assertEqual((mine["status"], mine["decision_note"]), ("rejected", "We are short that day"))
        self.assertEqual(self.codes(self.decide(self.manager_a, "reject", swap.pk)), ["swap_not_open"])

    def test_manager_can_reject_before_the_colleague_has_answered(self):
        swap_id = self.request(self.emp_a.user, self.s_a0, self.emp_c).data["id"]
        self.assertEqual(self.decide(self.manager_a, "reject", swap_id).status_code, 200)
        self.assertEqual(self.codes(self.act(self.emp_c.user, "accept", swap_id)), ["swap_not_waiting"])

    def test_queue_is_scoped_and_defaults_to_waiting_swaps(self):
        waiting = self.waiting_for_manager()
        self.request(self.emp_a.user, self.s_a1, self.emp_c)  # still waiting for the colleague
        self.login(self.manager_a)
        queue = self.client.get(reverse("swap-queue"))
        self.assertEqual([s["id"] for s in queue.data], [waiting.pk])
        self.assertEqual(len(self.client.get(reverse("swap-queue"), {"status": "all"}).data), 2)
        self.assertEqual(self.client.get(reverse("swap-queue"), {"status": "nonsense"}).status_code, 400)
        for user in (self.manager_b, self.owner):
            self.login(user)
            expected = 0 if user == self.manager_b else 1
            self.assertEqual(len(self.client.get(reverse("swap-queue")).data), expected)
        self.login(self.emp_a.user)
        self.assertEqual(self.client.get(reverse("swap-queue")).status_code, 403)

    def test_queue_warns_what_would_stop_an_approval(self):
        swap = self.waiting_for_manager()
        visa_services.revoke_visa(visa=self.visa_c, by=self.hr, reason="x")
        self.login(self.manager_a)
        item = self.client.get(reverse("swap-queue")).data[0]
        self.assertEqual([p["code"] for p in item["problems_if_approved"]], ["not_cleared"])
        body = json.dumps(item).lower()
        for leak in ("visa", "revoked", "immigration"):
            self.assertNotIn(leak, body)


class PrivacyTests(SwapBase):
    def test_employees_never_see_manager_notes_or_email_addresses(self):
        swap = self.waiting_for_manager(in_return=self.s_c2)
        for user in (self.emp_a.user, self.emp_c.user):
            self.login(user)
            body = json.dumps(self.client.get(reverse("my-swaps")).data)
            self.assertNotIn("private manager note", body)
            self.assertNotIn("@example.com", body)
            self.assertNotIn('"notes"', body)
        self.assertEqual(swap.pk, ShiftSwap.objects.get().pk)

    def test_my_swaps_lists_only_my_own_and_filters(self):
        self.waiting_for_manager()
        third = self.hire("third@example.com", self.store_a)
        self.login(third.user)
        self.assertEqual(self.client.get(reverse("my-swaps")).data, [])
        self.login(self.emp_a.user)
        self.assertEqual(len(self.client.get(reverse("my-swaps"), {"status": "open"}).data), 1)
        self.assertEqual(len(self.client.get(reverse("my-swaps"), {"status": "approved"}).data), 0)
        self.assertEqual(self.client.get(reverse("my-swaps"), {"status": "bogus"}).status_code, 400)
