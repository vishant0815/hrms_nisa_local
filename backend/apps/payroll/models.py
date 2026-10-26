from django.conf import settings
from django.core.files.storage import storages
from django.db import models


def private_storage():
    return storages["private_documents"]


def logo_path(instance, filename):
    return f"payslip-template/logo{filename[filename.rfind('.'):].lower()}"


class PayslipTemplate(models.Model):
    """The one set of wording, colours and logo that every payslip uses. Always the row with pk=1."""

    trading_name = models.CharField(max_length=80, default="Nisa Local")
    legal_employer_name = models.CharField(max_length=120, blank=True)
    employer_address = models.TextField(max_length=300, blank=True)
    paye_reference = models.CharField(max_length=30, blank=True)
    company_number = models.CharField(max_length=30, blank=True)
    hr_email = models.EmailField(blank=True)
    footer_note = models.TextField(
        max_length=400, blank=True,
        default="Please keep this payslip: it is a record of your pay and tax.",
    )
    accent_colour = models.CharField(max_length=7, default="#1d5c3a")
    show_bank_line = models.BooleanField(default=True)
    logo = models.FileField(upload_to=logo_path, storage=private_storage, max_length=200, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def current(cls):
        template, _ = cls.objects.get_or_create(pk=1)
        return template

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise models.ProtectedError("The payslip template cannot be deleted; edit it instead.", {self})
