"""The company logo: what may be uploaded and how it is stored.

A logo is shown to people who are not signed in (the sign-in page), so it is deliberately limited to plain image
types. SVG is refused because it can carry scripts. The real type is read from the file's first bytes, not from its
name or what the browser says, and every file is virus-scanned before it is stored.
"""
from rest_framework.exceptions import ValidationError

from . import scanning
from .models import SiteBranding

MAX_BYTES = 1024 * 1024
TYPES = {
    "image/png": ("png", lambda head: head.startswith(b"\x89PNG\r\n\x1a\n")),
    "image/jpeg": ("jpg", lambda head: head.startswith(b"\xff\xd8\xff")),
    "image/webp": ("webp", lambda head: head[:4] == b"RIFF" and head[8:12] == b"WEBP"),
}


def detect_type(upload):
    upload.seek(0)
    head = upload.read(16)
    upload.seek(0)
    for content_type, (_, matches) in TYPES.items():
        if matches(head):
            return content_type
    return None


def validate_logo(upload):
    """The content type of a good logo, or a ValidationError saying what is wrong."""
    if upload.size == 0:
        raise ValidationError({"file": "The file is empty."})
    if upload.size > MAX_BYTES:
        raise ValidationError({"file": "The logo is too large. The limit is 1 MB."})
    content_type = detect_type(upload)
    if content_type is None:
        raise ValidationError({"file": "Upload a PNG, JPEG or WebP image. SVG files are not accepted."})
    scanning.scan_upload(upload, "logo")
    return content_type


def set_logo(*, upload, by):
    content_type = validate_logo(upload)
    branding = SiteBranding.current()
    old = branding.logo.name
    upload.name = f"logo.{TYPES[content_type][0]}"
    branding.logo = upload
    branding.logo_content_type = content_type
    branding.updated_by = by
    branding.save()
    if old:
        branding.logo.storage.delete(old)
    return branding


def remove_logo(*, by):
    branding = SiteBranding.current()
    old = branding.logo.name
    branding.logo = ""
    branding.logo_content_type = ""
    branding.updated_by = by
    branding.save()
    if old:
        branding.logo.storage.delete(old)
    return branding


def set_name(*, name, by):
    name = (name or "").strip()
    if not name:
        raise ValidationError({"company_name": "Enter the name to show."})
    if len(name) > 60:
        raise ValidationError({"company_name": "Use 60 characters or fewer."})
    branding = SiteBranding.current()
    branding.company_name = name
    branding.updated_by = by
    branding.save()
    return branding
