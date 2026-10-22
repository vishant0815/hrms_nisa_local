from datetime import date

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.core.testutils import days_from_today, make_pdf, make_text_pdf, verified_term_letter

from . import reading
from .rules import Code
from .test_term_letters import TermLetterBase

TODAY = days_from_today(0)


def fmt(d):
    return f"{d.day} {d.strftime('%B %Y')}"


class DateParsingTests(TermLetterBase):
    def test_common_uk_date_styles_are_understood(self):
        today = date(2026, 10, 22)
        for text in ("1 October 2026", "1st Oct 2026", "October 1, 2026", "01/10/2026", "2026-10-01", "1.10.2026"):
            with self.subTest(text=text):
                self.assertEqual(reading.dates_in(text, today), [date(2026, 10, 1)])

    def test_impossible_and_far_away_dates_are_ignored(self):
        today = date(2026, 10, 22)
        self.assertEqual(reading.dates_in("31 February 2026 and 12/13/2026 and 1 May 1999", today), [])

    def test_term_and_vacation_dates_are_split(self):
        text = "\n".join([
            "To whom it may concern",
            "Date: 3 September 2026",
            "Our student is enrolled from 21 September 2026 until 18 June 2027.",
            "Christmas vacation: 14 December 2026 to 8 January 2027",
            "Easter break 29 March 2027 - 16 April 2027",
            "Summer vacation 1 September 2027 to 20 September 2027",
        ])
        result = reading.suggest_dates(text, date(2026, 10, 22))
        self.assertEqual((result["covers_from"], result["covers_to"]), ("2026-09-21", "2027-06-18"))
        self.assertEqual([(v["start_date"], v["end_date"]) for v in result["vacations"]], [
            ("2026-12-14", "2027-01-08"), ("2027-03-29", "2027-04-16"),
        ])  # the summer one is outside the covered dates, so it is not suggested
        self.assertIn("Christmas", result["vacations"][0]["label"])

    def test_the_letter_date_is_not_mistaken_for_the_term_start(self):
        result = reading.suggest_dates("Date: 3 September 2026\nTerm runs 21 September 2026 to 18 June 2027",
                                       date(2026, 10, 22))
        self.assertEqual(result["covers_from"], "2026-09-21")

    def test_text_with_no_dates_gives_no_suggestion(self):
        self.assertEqual(reading.suggest_dates("Hello, this letter has no dates."), {})
        self.assertEqual(reading.suggest_dates(""), {})


class FileReadingTests(TermLetterBase):
    def test_text_pdf_is_read(self):
        pdf = make_text_pdf([
            "Student enrolment letter",
            f"Course start {fmt(days_from_today(-30))} and end {fmt(days_from_today(200))}",
            f"Winter vacation {fmt(days_from_today(40))} to {fmt(days_from_today(60))}",
        ])
        text = reading.extract_text(pdf, "application/pdf")
        self.assertIn("Student enrolment letter", text)

    def test_images_and_broken_pdfs_give_no_text_and_no_crash(self):
        png = SimpleUploadedFile("a.png", b"\x89PNG\r\n\x1a\n" + b"0" * 30)
        self.assertEqual(reading.extract_text(png, "image/png"), "")
        broken = SimpleUploadedFile("a.pdf", b"%PDF-1.4 this is not really a pdf")
        self.assertEqual(reading.extract_text(broken, "application/pdf"), "")
        self.assertEqual(reading.suggestions_for(broken, "application/pdf"), {})

    def test_upload_stores_suggestions_for_hr_only(self):
        pdf = make_text_pdf([
            f"Enrolled from {fmt(days_from_today(-30))} until {fmt(days_from_today(200))}",
            f"Christmas vacation {fmt(days_from_today(40))} to {fmt(days_from_today(60))}",
        ])
        letter_id = self.post_letter(file=pdf).data["id"]
        self.assertNotIn("suggested", self.client.get(reverse("my-term-letters")).data[0])
        self.login(self.hr)
        suggested = self.client.get(reverse("term-letter-queue")).data[0]["suggested"]
        self.assertEqual(suggested["covers_from"], days_from_today(-30).isoformat())
        self.assertEqual(suggested["covers_to"], days_from_today(200).isoformat())
        self.assertEqual(len(suggested["vacations"]), 1)
        self.assertEqual(letter_id, self.client.get(reverse("term-letter-queue")).data[0]["id"])

    def test_a_suggestion_changes_nothing_until_hr_confirms(self):
        pdf = make_text_pdf([f"Enrolled from {fmt(days_from_today(-30))} until {fmt(days_from_today(200))}"])
        self.post_letter(file=pdf)
        self.assertTrue(self.decide().paperwork_needed)
        self.assertEqual(self.decide(week_hours=30).codes, {Code.HOURS_LIMIT})

    def test_upload_still_works_when_nothing_can_be_read(self):
        response = self.post_letter(file=make_pdf())
        self.assertEqual(response.status_code, 201)
        self.login(self.hr)
        self.assertEqual(self.client.get(reverse("term-letter-queue")).data[0]["suggested"], {})


class AvailabilityTests(TermLetterBase):
    def url(self, employee=None):
        return reverse("employee-availability", args=[(employee or self.employee).pk])

    def get(self, user, **params):
        self.login(user)
        return self.client.get(self.url(), params)

    def test_term_time_is_limited_and_vacation_is_full_time(self):
        verified_term_letter(self.visa, self.hr, days_from_today(-30), days_from_today(100), [
            {"start_date": days_from_today(10), "end_date": days_from_today(19), "label": "Winter"},
        ])
        response = self.get(self.manager_a, start=TODAY.isoformat(), end=days_from_today(29).isoformat())
        self.assertEqual(response.status_code, 200, response.data)
        modes = [(p["mode"], p["weekly_hours_limit"], str(p["start"]), str(p["end"])) for p in response.data["periods"]]
        self.assertEqual(modes, [
            ("limited", 20, str(TODAY), str(days_from_today(9))),
            ("full_time", None, str(days_from_today(10)), str(days_from_today(19))),
            ("limited", 20, str(days_from_today(20)), str(days_from_today(29))),
        ])
        self.assertFalse(any(p["paperwork_needed"] for p in response.data["periods"]))

    def test_periods_split_where_the_letter_stops_covering(self):
        verified_term_letter(self.visa, self.hr, days_from_today(-30), days_from_today(4))
        response = self.get(self.manager_a, start=TODAY.isoformat(), end=days_from_today(9).isoformat())
        self.assertEqual(
            [(p["mode"], p["paperwork_needed"]) for p in response.data["periods"]],
            [("limited", False), ("limited", True)],
        )

    def test_no_letter_means_limited_with_paperwork_flag(self):
        response = self.get(self.manager_a, start=TODAY.isoformat(), end=days_from_today(5).isoformat())
        self.assertEqual(len(response.data["periods"]), 1)
        self.assertEqual(response.data["periods"][0]["mode"], "limited")
        self.assertTrue(response.data["periods"][0]["paperwork_needed"])

    def test_someone_not_cleared_is_reported_without_a_reason(self):
        other = self.hire("nc@example.com", self.store_a)
        self.give_visa(other, "Skilled Worker", expiry=days_from_today(-2), check="fail")
        self.login(self.manager_a)
        response = self.client.get(self.url(other), {"end": days_from_today(3).isoformat()})
        self.assertEqual(response.data["periods"][0]["mode"], "not_cleared")
        self.assertNotIn("reasons", str(response.data))

    def test_manager_sees_no_letter_or_visa_details(self):
        verified_term_letter(self.visa, self.hr, days_from_today(-30), days_from_today(100))
        body = str(self.get(self.manager_a).data).lower()
        for leak in ("student", "letter", "university", "visa", "vacation"):
            self.assertNotIn(leak, body)

    def test_access_and_limits(self):
        self.assertEqual(self.get(self.manager_b).status_code, 404)  # another shop's employee
        self.assertEqual(self.get(self.employee.user).status_code, 403)
        self.assertEqual(self.get(self.owner).status_code, 200)
        self.assertEqual(self.get(self.manager_a, end=days_from_today(-3).isoformat()).status_code, 400)
        self.assertEqual(self.get(self.manager_a, end=days_from_today(120).isoformat()).status_code, 400)
        self.assertEqual(self.get(self.manager_a, end="nonsense").status_code, 400)
