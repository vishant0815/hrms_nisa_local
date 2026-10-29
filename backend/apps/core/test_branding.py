import os

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import Role

from .models import SiteBranding
from .testutils import make_user

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 200
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 200
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"0" * 200


def image(name="logo.png", body=PNG, content_type="image/png"):
    return SimpleUploadedFile(name, body, content_type=content_type)


class BrandingTests(TestCase):
    def setUp(self):
        self.hr = make_user("hr@example.com", Role.HR)
        self.owner = make_user("owner@example.com", Role.OWNER)
        self.manager = make_user("m@example.com", Role.MANAGER)
        self.staff = make_user("s@example.com", Role.EMPLOYEE)

    def as_user(self, user=None):
        c = APIClient()
        if user:
            c.force_authenticate(user)
        return c

    def upload(self, user, file):
        return self.as_user(user).post("/api/branding/edit/", {"file": file}, format="multipart")

    def test_anyone_can_see_the_name_and_logo_before_signing_in(self):
        r = self.as_user().get("/api/branding/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["company_name"], "Nisa Local")
        self.assertFalse(r.json()["has_logo"])
        self.assertEqual(self.as_user().get("/api/branding/logo/").status_code, 404)

    def test_a_bad_sign_in_token_does_not_break_the_public_pages(self):
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")
        self.assertEqual(c.get("/api/branding/").status_code, 200)

    def test_hr_and_owner_can_upload_and_everyone_can_then_fetch_it(self):
        for user, kind in ((self.hr, (PNG, "image/png")), (self.owner, (JPEG, "image/jpeg"))):
            r = self.upload(user, image("a." + kind[1][-3:], kind[0], kind[1]))
            self.assertEqual(r.status_code, 201, r.content)
            self.assertTrue(r.json()["has_logo"])
            got = self.as_user().get("/api/branding/logo/")
            self.assertEqual(got.status_code, 200)
            self.assertEqual(got["Content-Type"], kind[1])
            self.assertEqual(got["X-Content-Type-Options"], "nosniff")
            self.assertEqual(b"".join(got.streaming_content)[:4], kind[0][:4])

    def test_webp_is_accepted(self):
        self.assertEqual(self.upload(self.hr, image("l.webp", WEBP, "image/webp")).status_code, 201)

    def test_only_owner_and_hr_can_change_it(self):
        self.assertEqual(self.upload(self.manager, image()).status_code, 403)
        self.assertEqual(self.upload(self.staff, image()).status_code, 403)
        self.assertEqual(self.upload(None, image()).status_code, 401)
        self.assertEqual(self.as_user(self.manager).patch("/api/branding/edit/", {"company_name": "X"}).status_code, 403)
        self.assertEqual(self.as_user(self.staff).delete("/api/branding/edit/").status_code, 403)
        self.assertFalse(SiteBranding.current().logo)

    def test_svg_and_disguised_files_are_refused(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        self.assertEqual(self.upload(self.hr, image("logo.svg", svg, "image/svg+xml")).status_code, 400)
        # a text file with a .png name and a png content type
        self.assertEqual(self.upload(self.hr, image("logo.png", b"not an image at all")).status_code, 400)
        self.assertEqual(self.upload(self.hr, image("logo.png", b"")).status_code, 400)
        self.assertFalse(SiteBranding.current().logo)

    def test_a_large_file_is_refused(self):
        r = self.upload(self.hr, image("big.png", PNG + b"0" * (1024 * 1024)))
        self.assertEqual(r.status_code, 400)
        self.assertIn("1 MB", str(r.json()))

    def test_no_file_is_a_clear_error(self):
        r = self.as_user(self.hr).post("/api/branding/edit/", {}, format="multipart")
        self.assertEqual(r.status_code, 400)

    def test_replacing_the_logo_removes_the_old_file_and_changes_the_version(self):
        self.upload(self.hr, image())
        first = SiteBranding.current()
        old_path, old_version = first.logo.path, self.as_user().get("/api/branding/").json()["version"]
        self.assertTrue(os.path.exists(old_path))
        self.upload(self.hr, image("new.jpg", JPEG, "image/jpeg"))
        self.assertFalse(os.path.exists(old_path))
        self.assertTrue(os.path.exists(SiteBranding.current().logo.path))
        self.assertGreaterEqual(self.as_user().get("/api/branding/").json()["version"], old_version)
        self.assertEqual(SiteBranding.current().updated_by, self.hr)

    def test_the_stored_name_is_random_not_what_was_uploaded(self):
        self.upload(self.hr, image("../../evil name.png"))
        name = SiteBranding.current().logo.name
        self.assertTrue(name.startswith("branding/"))
        self.assertNotIn("evil", name)
        self.assertNotIn("..", name)

    def test_removing_the_logo(self):
        self.upload(self.hr, image())
        path = SiteBranding.current().logo.path
        r = self.as_user(self.hr).delete("/api/branding/edit/")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["has_logo"])
        self.assertFalse(os.path.exists(path))
        self.assertEqual(self.as_user().get("/api/branding/logo/").status_code, 404)

    def test_changing_the_name(self):
        r = self.as_user(self.owner).patch("/api/branding/edit/", {"company_name": "  Nisa Local Hackney "}, format="json")
        self.assertEqual(r.json()["company_name"], "Nisa Local Hackney")
        self.assertEqual(self.as_user().get("/api/branding/").json()["company_name"], "Nisa Local Hackney")
        self.assertEqual(self.as_user(self.owner).patch("/api/branding/edit/", {"company_name": "  "}, format="json").status_code, 400)
        self.assertEqual(self.as_user(self.owner).patch("/api/branding/edit/", {"company_name": "x" * 61}, format="json").status_code, 400)

    def test_there_is_only_ever_one_row(self):
        SiteBranding.current()
        SiteBranding(company_name="Another").save()
        self.assertEqual(SiteBranding.objects.count(), 1)


class BrandingAdminTests(TestCase):
    def test_the_admin_form_applies_the_same_checks(self):
        from .admin import BrandingForm

        site = SiteBranding.current()
        bad = BrandingForm({"company_name": "Nisa Local"}, {"logo": image("x.png", b"not an image")}, instance=site)
        self.assertFalse(bad.is_valid())
        self.assertIn("logo", bad.errors)
        svg = BrandingForm({"company_name": "Nisa Local"}, {"logo": image("x.svg", b"<svg/>", "image/svg+xml")}, instance=site)
        self.assertFalse(svg.is_valid())
        good = BrandingForm({"company_name": "Nisa Local"}, {"logo": image()}, instance=site)
        self.assertTrue(good.is_valid(), good.errors)


from django.test import override_settings  # noqa: E402


class BrandingScanningTests(TestCase):
    @override_settings(UPLOAD_SCANNING="clamd", CLAMAV_HOST="127.0.0.1", CLAMAV_PORT=1, CLAMAV_TIMEOUT=1)
    def test_a_logo_is_refused_when_the_virus_scanner_cannot_be_reached(self):
        hr = make_user("hr@example.com", Role.HR)
        c = APIClient()
        c.force_authenticate(hr)
        r = c.post("/api/branding/edit/", {"file": image()}, format="multipart")
        self.assertEqual(r.status_code, 503)
        self.assertFalse(SiteBranding.current().logo)
