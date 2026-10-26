from io import BytesIO

from django.core.files.base import ContentFile
from PIL import Image, UnidentifiedImageError
from rest_framework.exceptions import ValidationError

from . import rules
from .models import PayslipTemplate


def update_template(user, data: dict):
    template = PayslipTemplate.current()
    for field, value in data.items():
        setattr(template, field, value)
    template.updated_by = user
    template.save()
    return template


def check_logo(raw: bytes):
    """Return (content_type, extension) for an acceptable logo, or raise a readable error."""
    if not raw:
        raise ValidationError({"logo": "The file is empty."})
    if len(raw) > rules.MAX_LOGO_BYTES:
        raise ValidationError({"logo": "The logo must be 1 MB or smaller."})
    try:
        with Image.open(BytesIO(raw)) as image:
            image.load()  # decodes fully, so a damaged or disguised file fails here
            kind, size = image.format, image.size
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValidationError({"logo": "That file is not a PNG or JPEG picture."})
    if kind not in rules.LOGO_TYPES:
        raise ValidationError({"logo": "Use a PNG or JPEG picture."})
    if max(size) > rules.MAX_LOGO_SIDE:
        raise ValidationError({"logo": f"The logo can be at most {rules.MAX_LOGO_SIDE} pixels on its longest side."})
    return rules.LOGO_TYPES[kind]


def set_logo(user, raw: bytes):
    content_type, extension = check_logo(raw)
    template = PayslipTemplate.current()
    if template.logo:
        template.logo.delete(save=False)
    template.logo.save(f"logo{extension}", ContentFile(raw), save=False)
    template.updated_by = user
    template.save()
    return template, content_type


def clear_logo(user):
    template = PayslipTemplate.current()
    if template.logo:
        template.logo.delete(save=False)
    template.logo = ""
    template.updated_by = user
    template.save()
    return template
