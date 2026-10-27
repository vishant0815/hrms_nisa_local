from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from django.urls import reverse
from PIL import Image
from rest_framework.test import APITestCase

from apps.accounts.models import Role
from apps.core.testutils import make_user

from . import rules
from .models import PayslipTemplate


def picture(kind="PNG", size=(120, 40)):
    buffer = BytesIO()
    Image.new("RGB", size, "#1d5c3a").save(buffer, kind)
    return buffer.getvalue()


class AccentRuleTests(SimpleTestCase):
    def test_dark_colours_pass_and_light_ones_do_not(self):
        self.assertIsNone(rules.accent_problem("#1d5c3a"))
        self.assertIsNone(rules.accent_problem("#000000"))
        self.assertIn("too light", rules.accent_problem("#ffff00"))
        self.assertIn("too light", rules.accent_problem("#ffffff"))

    def test_the_contrast_limit_sits_where_the_standard_puts_it(self):
        self.assertAlmostEqual(rules.contrast_with_white("#767676"), 4.54, places=1)
        self.assertIsNone(rules.accent_problem("#767676"))
        self.assertIsNotNone(rules.accent_problem("#777777"))

    def test_only_six_digit_hex_is_a_colour(self):
        for bad in ["", "red", "#abc", "#12345g", "1d5c3a", "#1d5c3a0", None]:
            self.assertIn("six", rules.accent_problem(bad), bad)


class TemplateApiTests(APITestCase):
    def setUp(self):
        self.owner = make_user("o@x.com", Role.OWNER)
        self.hr = make_user("h@x.com", Role.HR)
        self.manager = make_user("m@x.com", Role.MANAGER)
        self.employee = make_user("e@x.com", Role.EMPLOYEE)
        self.url = reverse("payslip-template")
        self.logo_url = reverse("payslip-template-logo")

    def as_user(self, user):
        self.client.force_authenticate(user)

    def test_there_is_a_sensible_template_before_anyone_edits_it(self):
        self.as_user(self.owner)
        data = self.client.get(self.url).json()
        self.assertEqual(data["trading_name"], "Nisa Local")
        self.assertEqual(data["accent_colour"], "#1d5c3a")
        self.assertTrue(data["show_bank_line"])
        self.assertFalse(data["has_logo"])
        self.assertEqual(PayslipTemplate.objects.count(), 1)

    def test_owner_and_hr_can_edit_everyone_else_is_refused(self):
        for user in (self.owner, self.hr):
            self.as_user(user)
            response = self.client.patch(self.url, {"legal_employer_name": f"Shop Ltd {user.role}"}, format="json")
            self.assertEqual(response.status_code, 200)
        for user in (self.manager, self.employee):
            self.as_user(user)
            self.assertEqual(self.client.get(self.url).status_code, 403)
            self.assertEqual(self.client.patch(self.url, {"trading_name": "Hacked"}, format="json").status_code, 403)
            self.assertEqual(self.client.get(self.logo_url).status_code, 403)
            self.assertEqual(self.client.delete(self.logo_url).status_code, 403)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.url).status_code, 401)
        self.assertEqual(PayslipTemplate.current().trading_name, "Nisa Local")

    def test_edits_are_saved_and_remember_who_made_them(self):
        self.as_user(self.hr)
        body = {
            "trading_name": " Nisa Local Hackney ", "legal_employer_name": "Shop Ltd", "employer_address": "1 High St",
            "paye_reference": "123/AB456", "company_number": "01234567", "hr_email": "hr@shop.example",
            "footer_note": "Keep this.", "accent_colour": "#1F4E79", "show_bank_line": False,
        }
        data = self.client.patch(self.url, body, format="json").json()
        template = PayslipTemplate.current()
        self.assertEqual(template.trading_name, "Nisa Local Hackney")
        self.assertEqual(template.accent_colour, "#1f4e79")
        self.assertFalse(template.show_bank_line)
        self.assertEqual(template.paye_reference, "123/AB456")
        self.assertEqual(template.updated_by, self.hr)
        self.assertEqual(data["hr_email"], "hr@shop.example")

    def test_a_partial_edit_leaves_the_other_fields_alone(self):
        self.as_user(self.owner)
        self.client.patch(self.url, {"paye_reference": "123/AB456"}, format="json")
        self.client.patch(self.url, {"company_number": "999"}, format="json")
        template = PayslipTemplate.current()
        self.assertEqual((template.paye_reference, template.company_number), ("123/AB456", "999"))

    def test_bad_values_are_refused_with_a_reason(self):
        self.as_user(self.owner)
        self.assertEqual(self.client.patch(self.url, {"trading_name": "   "}, format="json").status_code, 400)
        self.assertEqual(self.client.patch(self.url, {"hr_email": "nope"}, format="json").status_code, 400)
        response = self.client.patch(self.url, {"accent_colour": "#ffee00"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("too light", response.json()["accent_colour"][0])
        self.assertEqual(PayslipTemplate.current().accent_colour, "#1d5c3a")

    def test_the_template_cannot_be_read_only_fields_overwritten(self):
        self.as_user(self.owner)
        self.client.patch(self.url, {"has_logo": True, "updated_at": "2000-01-01T00:00:00Z"}, format="json")
        self.assertFalse(self.client.get(self.url).json()["has_logo"])
        self.assertGreater(PayslipTemplate.current().updated_at.year, 2000)


class LogoTests(APITestCase):
    def setUp(self):
        self.owner = make_user("o@x.com", Role.OWNER)
        self.client.force_authenticate(self.owner)
        self.url = reverse("payslip-template-logo")

    def upload(self, raw, name="logo.png"):
        return self.client.post(self.url, {"logo": SimpleUploadedFile(name, raw)}, format="multipart")

    def test_a_png_logo_can_be_uploaded_read_back_and_removed(self):
        raw = picture("PNG")
        response = self.upload(raw)
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()["has_logo"])
        got = self.client.get(self.url)
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got["Content-Type"], "image/png")
        self.assertEqual(got["X-Content-Type-Options"], "nosniff")
        self.assertIn("no-store", got["Cache-Control"])
        self.assertEqual(got.content, raw)
        self.assertEqual(PayslipTemplate.current().updated_by, self.owner)
        PayslipTemplate.objects.update(updated_by=None)
        removed = self.client.delete(self.url).json()
        self.assertFalse(removed["has_logo"])
        self.assertEqual(PayslipTemplate.current().updated_by, self.owner)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_a_jpeg_works_and_a_new_logo_replaces_the_old_one(self):
        self.upload(picture("PNG"))
        old = PayslipTemplate.current().logo.name
        self.assertEqual(self.upload(picture("JPEG"), "logo.jpg").status_code, 201)
        template = PayslipTemplate.current()
        self.assertTrue(template.logo.name.endswith(".jpg"))
        self.assertFalse(template.logo.storage.exists(old))
        self.assertEqual(self.client.get(self.url)["Content-Type"], "image/jpeg")

    def test_the_file_type_comes_from_the_picture_not_the_name(self):
        self.assertEqual(self.upload(picture("JPEG"), "logo.png").status_code, 201)
        self.assertEqual(self.client.get(self.url)["Content-Type"], "image/jpeg")
        self.assertTrue(PayslipTemplate.current().logo.name.endswith(".jpg"))

    def test_things_that_are_not_png_or_jpeg_pictures_are_refused(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        for raw in (b"", b"hello", svg, b"%PDF-1.4 fake", picture("GIF"), picture("BMP")):
            response = self.upload(raw, "logo.png")
            self.assertEqual(response.status_code, 400, raw[:12])
        self.assertFalse(PayslipTemplate.current().logo)

    def test_a_truncated_picture_is_refused(self):
        self.assertEqual(self.upload(picture("PNG")[:-30]).status_code, 400)

    def test_size_limits(self):
        self.assertEqual(self.upload(picture("PNG", (2000, 10))).status_code, 201)
        self.assertEqual(self.upload(picture("PNG", (2001, 10))).status_code, 400)
        big = picture("PNG", (10, 10)) + b"\0" * (1024 * 1024)
        self.assertEqual(self.upload(big).status_code, 400)

    def test_a_missing_field_is_a_clear_error(self):
        response = self.client.post(self.url, {}, format="multipart")
        self.assertEqual(response.status_code, 400)
        self.assertIn("logo", response.json())

    def test_an_infected_logo_is_refused_and_the_scanner_being_down_refuses_too(self):
        from django.test import override_settings

        from apps.core.test_scanning import FakeClamd, settings_for

        self.upload(picture("PNG"))
        kept = PayslipTemplate.current().logo.name
        fake = FakeClamd(answer=b"stream: Win.Test.EICAR_HDB-1 FOUND")
        with settings_for(fake.port):
            self.assertEqual(self.upload(picture("PNG")).status_code, 400)
        with override_settings(UPLOAD_SCANNING="clamd", CLAMAV_HOST="127.0.0.1", CLAMAV_PORT=1, CLAMAV_TIMEOUT=1):
            self.assertEqual(self.upload(picture("PNG")).status_code, 503)
        self.assertEqual(PayslipTemplate.current().logo.name, kept)

    def test_a_failed_upload_keeps_the_old_logo(self):
        self.upload(picture("PNG"))
        kept = PayslipTemplate.current().logo.name
        self.assertEqual(self.upload(b"junk").status_code, 400)
        self.assertEqual(PayslipTemplate.current().logo.name, kept)

    def test_a_missing_stored_file_reports_not_found(self):
        self.upload(picture("PNG"))
        template = PayslipTemplate.current()
        template.logo.storage.delete(template.logo.name)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_the_logo_is_not_reachable_without_signing_in(self):
        self.upload(picture("PNG"))
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.url).status_code, 401)
        self.assertEqual(self.upload(picture("PNG")).status_code, 401)


class SingletonTests(APITestCase):
    def test_there_is_only_ever_one_template_and_it_cannot_be_deleted(self):
        PayslipTemplate(trading_name="A").save()
        PayslipTemplate(trading_name="B").save()
        self.assertEqual(PayslipTemplate.objects.count(), 1)
        self.assertEqual(PayslipTemplate.current().trading_name, "B")
        from django.db.models import ProtectedError
        with self.assertRaises(ProtectedError):
            PayslipTemplate.current().delete()
        self.assertEqual(PayslipTemplate.objects.count(), 1)

    def test_a_second_row_is_refused_by_the_database(self):
        from django.db import IntegrityError, transaction
        PayslipTemplate.current()
        with self.assertRaises(IntegrityError), transaction.atomic():
            PayslipTemplate.objects.create(trading_name="Second")
