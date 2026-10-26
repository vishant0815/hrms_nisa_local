from django.contrib import admin

from .models import PayslipTemplate


@admin.register(PayslipTemplate)
class PayslipTemplateAdmin(admin.ModelAdmin):
    """Edit the wording and colour here. The logo goes through the API, which checks the picture."""

    list_display = ["trading_name", "legal_employer_name", "accent_colour", "updated_at"]
    readonly_fields = ["logo", "updated_by", "updated_at"]

    def has_add_permission(self, request):
        return not PayslipTemplate.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
