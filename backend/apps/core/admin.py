from django import forms
from django.core.files.uploadedfile import UploadedFile
from django.contrib import admin
from rest_framework.exceptions import ValidationError as ApiValidationError

from . import branding
from .models import SiteBranding


class BrandingForm(forms.ModelForm):
    """The same checks as the website's upload: image type, size, no SVG, virus scan."""

    class Meta:
        model = SiteBranding
        fields = ["company_name", "logo"]

    def clean_logo(self):
        logo = self.cleaned_data.get("logo")
        if isinstance(logo, UploadedFile):  # a new file; keeping the current one passes through
            try:
                content_type = branding.validate_logo(logo)
            except ApiValidationError as problem:
                raise forms.ValidationError(str(problem.detail.get("file", problem.detail)[0] if isinstance(problem.detail, dict) else problem.detail))
            logo.name = f"logo.{branding.TYPES[content_type][0]}"
            self.instance.logo_content_type = content_type
        return logo


@admin.register(SiteBranding)
class SiteBrandingAdmin(admin.ModelAdmin):
    form = BrandingForm
    list_display = ("company_name", "updated_at")

    def has_add_permission(self, request):
        return not SiteBranding.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
