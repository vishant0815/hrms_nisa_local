import json
import os
from decimal import Decimal

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.core.testutils import days_from_today, make_pdf, verified_term_letter

from .models import LetterStatus, TermLetter, VacationPeriod, VisaAuditLog
from .rules import Code
from .tests import VisaTestBase

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 50
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 50


def upload(content, name="letter.pdf", content_type="application/pdf"):
    return SimpleUploadedFile(name, content, content_type=content_type)


class TermLetterBase(VisaTestBase):
    def setUp(self):
        super().setUp()
        cache.clear()  # the upload throttle is counted in the cache
        self.visa = self.give_visa(self.employee, "Student", expiry=days_from_today(300))

    def post_letter(self, user=None, file=None, **extra):
        self.login(user or self.employee.user)
        data = {"file": file if file is not None else make_pdf(), "institution": "City University", **extra}
        return self.client.post(reverse("my-term-letters"), data, format="multipart")

    def pending_letter(self):
        return self.post_letter().data["id"]

    def verify(self, letter_id, **body):
        body.setdefault("covers_from", days_from_today(-30).isoformat())
        body.setdefault("covers_to", days_from_today(100).isoformat())
        return self.client.post(reverse("term-letter-verify", args=[letter_id]), body, format="json")


class SeedTests(TermLetterBase):
    def test_only_the_student_status_asks_for_a_term_letter(self):
        from .models import VisaType

        flagged = set(VisaType.objects.filter(requires_term_letter=True).values_list("name", flat=True))
        self.assertEqual(flagged, {"Student"})


class UploadScanningTests(TermLetterBase):
    def test_an_infected_letter_is_refused_and_nothing_is_stored(self):
        from apps.core.test_scanning import FakeClamd, settings_for

        fake = FakeClamd()
        with settings_for(fake.port):
            response = self.post_letter(file=upload(b"%PDF-1.4 EICAR-STANDARD-ANTIVIRUS-TEST-FILE"))
        self.assertEqual(response.status_code, 400)
        self.assertIn("virus", json.dumps(response.data))
        self.assertEqual(TermLetter.objects.count(), 0)

    def test_a_clean_letter_passes_through_the_scanner(self):
        from apps.core.test_scanning import FakeClamd, settings_for

        fake = FakeClamd()
        with settings_for(fake.port):
            response = self.post_letter(file=upload(b"%PDF-1.4 clean"))
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(fake.received, b"%PDF-1.4 clean")

    def test_with_the_scanner_down_the_upload_is_refused_not_stored_unchecked(self):
        from django.test import override_settings

        with override_settings(UPLOAD_SCANNING="clamd", CLAMAV_HOST="127.0.0.1", CLAMAV_PORT=1, CLAMAV_TIMEOUT=1):
            response = self.post_letter()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(TermLetter.objects.count(), 0)


class UploadTests(TermLetterBase):
    def test_student_can_upload_pdf_png_and_jpeg(self):
        for content in (b"%PDF-1.7 hello", PNG, JPEG):
            with self.subTest(start=content[:4]):
                cache.clear()
                TermLetter.objects.all().delete()
                response = self.post_letter(file=upload(content, "x.bin", "application/octet-stream"))
                self.assertEqual(response.status_code, 201, response.data)
                self.assertEqual(response.data["status"], "pending")

    def test_the_type_comes_from_the_file_not_its_name_or_claimed_type(self):
        fake = upload(b"<html><script>alert(1)</script></html>", "letter.pdf", "application/pdf")
        response = self.post_letter(file=fake)
        self.assertEqual(response.status_code, 400)
        self.assertIn("file", response.data)
        self.assertEqual(TermLetter.objects.count(), 0)

    def test_empty_and_oversized_files_are_refused(self):
        self.assertEqual(self.post_letter(file=upload(b"")).status_code, 400)
        big = upload(b"%PDF-" + b"0" * (5 * 1024 * 1024))
        self.assertEqual(self.post_letter(file=big).status_code, 400)
        just_fits = upload(b"%PDF-" + b"0" * (5 * 1024 * 1024 - 5))
        self.assertEqual(self.post_letter(file=just_fits).status_code, 201)

    def test_missing_file_is_a_clear_error(self):
        self.login(self.employee.user)
        response = self.client.post(reverse("my-term-letters"), {"institution": "X"}, format="multipart")
        self.assertEqual(response.status_code, 400)
        self.assertIn("file", response.data)

    def test_stored_name_is_random_and_the_shown_name_is_cleaned(self):
        evil = upload(b"%PDF-1", "../../etc/pass wd<script>.pdf")
        letter = TermLetter.objects.get(pk=self.post_letter(file=evil).data["id"])
        stored = os.path.basename(letter.file.name)
        self.assertRegex(stored, r"^[0-9a-f]{32}\.pdf$")
        self.assertNotIn("..", letter.file.name)
        for bad in ("/", "<", ">"):
            self.assertNotIn(bad, letter.original_filename)

    def test_only_three_letters_can_wait_at_once(self):
        for _ in range(3):
            self.assertEqual(self.post_letter().status_code, 201)
        self.assertEqual(self.post_letter().status_code, 400)

    def test_only_students_can_upload(self):
        other = self.hire("grad@example.com", self.store_a)
        self.give_visa(other, "Graduate", expiry=days_from_today(300))
        self.assertEqual(self.post_letter(user=other.user).status_code, 400)

    def test_person_without_any_status_cannot_upload(self):
        other = self.hire("none@example.com", self.store_a)
        self.assertEqual(self.post_letter(user=other.user).status_code, 400)

    def test_users_without_an_employee_profile_get_404(self):
        self.assertEqual(self.post_letter(user=self.hr).status_code, 404)

    def test_login_is_required(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(reverse("my-term-letters")).status_code, 401)

    def test_student_sees_only_their_own_letters_and_no_file_path(self):
        self.post_letter()
        other = self.hire("s2@example.com", self.store_a)
        self.give_visa(other, "Student", expiry=days_from_today(300))
        self.post_letter(user=other.user)
        self.login(self.employee.user)
        mine = self.client.get(reverse("my-term-letters")).data
        self.assertEqual(len(mine), 1)
        for hidden in ("file", "content_type", "size_bytes", "reviewed_by"):
            self.assertNotIn(hidden, mine[0])

    def test_upload_is_audited(self):
        self.post_letter()
        self.assertTrue(VisaAuditLog.objects.filter(action="term_letter_uploaded").exists())


class ReviewTests(TermLetterBase):
    def test_hr_verifies_and_enters_dates_and_vacations(self):
        letter_id = self.pending_letter()
        self.login(self.hr)
        response = self.verify(letter_id, vacations=[
            {"start_date": days_from_today(10).isoformat(), "end_date": days_from_today(20).isoformat(),
             "label": "Winter"},
        ])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "verified")
        self.assertEqual(len(response.data["vacations"]), 1)
        self.assertFalse(self.decide().paperwork_needed)

    def test_verified_letter_only_covers_its_dates(self):
        letter_id = self.pending_letter()
        self.login(self.hr)
        self.verify(letter_id, covers_from=days_from_today(-30).isoformat(), covers_to=days_from_today(10).isoformat())
        self.assertFalse(self.decide(on=days_from_today(10)).paperwork_needed)
        self.assertTrue(self.decide(on=days_from_today(11)).paperwork_needed)
        self.assertTrue(self.decide(on=days_from_today(-31)).paperwork_needed)

    def test_bad_dates_are_refused(self):
        letter_id = self.pending_letter()
        self.login(self.hr)
        backwards = self.verify(letter_id, covers_from=days_from_today(10).isoformat(),
                                covers_to=days_from_today(1).isoformat())
        self.assertEqual(backwards.status_code, 400)
        outside = self.verify(letter_id, vacations=[
            {"start_date": days_from_today(90).isoformat(), "end_date": days_from_today(120).isoformat()}])
        self.assertEqual(outside.status_code, 400)
        reversed_vacation = self.verify(letter_id, vacations=[
            {"start_date": days_from_today(20).isoformat(), "end_date": days_from_today(10).isoformat()}])
        self.assertEqual(reversed_vacation.status_code, 400)
        self.assertEqual(TermLetter.objects.get(pk=letter_id).status, LetterStatus.PENDING)
        self.assertEqual(VacationPeriod.objects.count(), 0)

    def test_a_letter_can_only_be_reviewed_once(self):
        letter_id = self.pending_letter()
        self.login(self.hr)
        self.assertEqual(self.verify(letter_id).status_code, 200)
        self.assertEqual(self.verify(letter_id).status_code, 400)
        reject = self.client.post(reverse("term-letter-reject", args=[letter_id]), {"reason": "no"}, format="json")
        self.assertEqual(reject.status_code, 400)

    def test_rejecting_needs_a_reason_and_the_student_sees_it(self):
        letter_id = self.pending_letter()
        self.login(self.hr)
        url = reverse("term-letter-reject", args=[letter_id])
        self.assertEqual(self.client.post(url, {}, format="json").status_code, 400)
        self.assertEqual(self.client.post(url, {"reason": "   "}, format="json").status_code, 400)
        self.assertEqual(self.client.post(url, {"reason": "Dates unreadable"}, format="json").status_code, 200)
        self.login(self.employee.user)
        mine = self.client.get(reverse("my-term-letters")).data[0]
        self.assertEqual((mine["status"], mine["review_note"]), ("rejected", "Dates unreadable"))
        self.assertTrue(self.decide().paperwork_needed)

    def test_letter_for_an_old_status_cannot_be_verified(self):
        letter_id = self.pending_letter()
        self.give_visa(self.employee, "Graduate", expiry=days_from_today(300))
        self.login(self.hr)
        self.assertEqual(self.verify(letter_id).status_code, 400)

    def test_review_is_audited(self):
        letter_id = self.pending_letter()
        self.login(self.hr)
        self.verify(letter_id)
        self.assertTrue(VisaAuditLog.objects.filter(action="term_letter_verified", user=self.hr).exists())

    def test_queue_defaults_to_pending_and_filters(self):
        first = self.pending_letter()
        second = self.pending_letter()
        self.login(self.hr)
        self.verify(first)
        queue = self.client.get(reverse("term-letter-queue"))
        self.assertEqual([item["id"] for item in queue.data], [second])
        self.assertEqual(len(self.client.get(reverse("term-letter-queue"), {"status": "all"}).data), 2)
        self.assertEqual(len(self.client.get(reverse("term-letter-queue"), {"status": "verified"}).data), 1)
        self.assertEqual(self.client.get(reverse("term-letter-queue"), {"status": "nonsense"}).status_code, 400)

    def test_employee_letter_list_for_hr(self):
        self.pending_letter()
        self.login(self.owner)
        response = self.client.get(reverse("employee-term-letters", args=[self.employee.pk]))
        self.assertEqual(len(response.data), 1)

    def test_missing_letters_list(self):
        self.login(self.hr)
        url = reverse("visa-missing-term-letters")
        self.assertEqual([v["employee"]["id"] for v in self.client.get(url).data], [self.employee.pk])
        verified_term_letter(self.visa, self.hr, days_from_today(-30), days_from_today(100))
        self.assertEqual(self.client.get(url).data, [])


class DownloadAndAccessTests(TermLetterBase):
    def test_hr_can_download_the_file_and_it_is_audited(self):
        letter_id = self.post_letter(file=upload(b"%PDF-1.4 real content")).data["id"]
        self.login(self.hr)
        response = self.client.get(reverse("term-letter-file", args=[letter_id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"%PDF-1.4 real content")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(VisaAuditLog.objects.filter(action="term_letter_downloaded", user=self.hr).exists())

    def test_nobody_but_owner_and_hr_can_read_letters_or_files(self):
        letter_id = self.pending_letter()
        other = self.hire("peer@example.com", self.store_a)
        urls = [
            ("get", reverse("term-letter-file", args=[letter_id])),
            ("get", reverse("term-letter-queue")),
            ("get", reverse("employee-term-letters", args=[self.employee.pk])),
            ("get", reverse("visa-missing-term-letters")),
            ("post", reverse("term-letter-verify", args=[letter_id])),
            ("post", reverse("term-letter-reject", args=[letter_id])),
        ]
        for user in (self.manager_a, self.manager_b, other.user, self.employee.user):
            self.login(user)
            for method, url in urls:
                with self.subTest(user=user.email, url=url):
                    self.assertEqual(getattr(self.client, method)(url, {}, format="json").status_code, 403)
        self.assertEqual(TermLetter.objects.get(pk=letter_id).status, LetterStatus.PENDING)

    def test_missing_file_on_disk_is_a_404_not_a_crash(self):
        letter_id = self.pending_letter()
        letter = TermLetter.objects.get(pk=letter_id)
        letter.file.storage.delete(letter.file.name)
        self.login(self.hr)
        self.assertEqual(self.client.get(reverse("term-letter-file", args=[letter_id])).status_code, 404)


class RuleTests(TermLetterBase):
    def test_no_letter_warns_but_does_not_block(self):
        decision = self.decide(week_hours=Decimal("10"))
        self.assertTrue(decision.allowed)
        self.assertTrue(decision.paperwork_needed)
        self.assertTrue(any("term-dates letter" in w for w in decision.warnings))

    def test_pending_or_rejected_letters_do_not_count_as_covering(self):
        letter_id = self.pending_letter()
        self.assertTrue(self.decide().paperwork_needed)
        self.login(self.hr)
        self.client.post(reverse("term-letter-reject", args=[letter_id]), {"reason": "blurry"}, format="json")
        self.assertTrue(self.decide().paperwork_needed)

    def test_a_verified_letter_clears_the_warning(self):
        verified_term_letter(self.visa, self.hr, days_from_today(-30), days_from_today(100))
        decision = self.decide()
        self.assertFalse(decision.paperwork_needed)
        self.assertFalse(any("term-dates" in w for w in decision.warnings))

    def test_the_cap_still_applies_with_a_letter_and_no_vacation(self):
        verified_term_letter(self.visa, self.hr, days_from_today(-30), days_from_today(100))
        self.assertEqual(self.decide(week_hours=Decimal("25")).codes, {Code.HOURS_LIMIT})

    def test_a_vacation_only_lifts_the_cap_once_its_letter_is_verified(self):
        # A vacation row tied to a letter that was never verified must not lift the cap.
        letter_id = self.pending_letter()
        VacationPeriod.objects.create(
            visa=self.visa, term_letter_id=letter_id,
            start_date=days_from_today(-1), end_date=days_from_today(1),
        )
        self.assertEqual(self.decide(week_hours=Decimal("40")).codes, {Code.HOURS_LIMIT})

    def test_a_vacation_with_no_letter_at_all_does_not_lift_the_cap(self):
        VacationPeriod.objects.create(
            visa=self.visa, start_date=days_from_today(-1), end_date=days_from_today(1)
        )
        self.assertEqual(self.decide(week_hours=Decimal("40")).codes, {Code.HOURS_LIMIT})

    def test_other_statuses_never_need_a_letter(self):
        other = self.hire("g@example.com", self.store_a)
        self.give_visa(other, "Graduate", expiry=days_from_today(300))
        self.assertFalse(self.decide(employee=other).paperwork_needed)

    def test_manager_hears_only_that_paperwork_is_needed(self):
        self.login(self.manager_a)
        response = self.client.get(reverse("employee-can-work", args=[self.employee.pk]))
        self.assertTrue(response.data["paperwork_needed"])
        body = json.dumps(response.data, default=str).lower()
        for leak in ("student", "letter", "university", "term", "visa"):
            self.assertNotIn(leak, body)

    def test_visa_type_list_shows_the_flag_to_hr(self):
        self.login(self.hr)
        types = {t["name"]: t["requires_term_letter"] for t in self.client.get(reverse("visa-type-list")).data}
        self.assertTrue(types["Student"])
        self.assertFalse(types["Graduate"])
