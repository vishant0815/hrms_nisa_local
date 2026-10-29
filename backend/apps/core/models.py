import uuid

from django.conf import settings
from django.core.files.storage import storages
from django.db import models


def private_storage():
    return storages["private_documents"]


def logo_path(instance, filename):
    # The stored name is random and its extension comes from the checked file type.
    return f"branding/{uuid.uuid4().hex}.{filename.rsplit('.', 1)[-1]}"


class SiteBranding(models.Model):
    """The name and logo shown on every page. There is only ever one row (pk=1)."""

    company_name = models.CharField(max_length=60, default="Nisa Local")
    logo = models.FileField(upload_to=logo_path, storage=private_storage, max_length=200, blank=True)
    logo_content_type = models.CharField(max_length=20, blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "branding"
        verbose_name_plural = "branding"

    def __str__(self):
        return self.company_name

    @classmethod
    def current(cls):
        return cls.objects.get_or_create(pk=1)[0]

    def save(self, *args, **kwargs):
        self.pk = 1  # a single settings row
        super().save(*args, **kwargs)
