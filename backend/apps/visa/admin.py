from django.contrib import admin

from .models import EmployeeVisa, RightToWorkCheck, TermLetter, VacationPeriod, VisaAuditLog, VisaType


@admin.register(VisaType)
class VisaTypeAdmin(admin.ModelAdmin):
    list_display = ["name", "can_work", "requires_expiry", "max_weekly_hours", "is_active"]
    list_filter = ["can_work", "is_active"]


class VacationInline(admin.TabularInline):
    model = VacationPeriod
    extra = 0


@admin.register(EmployeeVisa)
class EmployeeVisaAdmin(admin.ModelAdmin):
    list_display = ["employee", "visa_type", "expiry_date", "is_current", "revoked_on"]
    list_filter = ["visa_type", "is_current"]
    inlines = [VacationInline]


class ReadOnlyAdmin(admin.ModelAdmin):
    """Audit trails can be viewed but never added to, changed or deleted by hand."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(TermLetter)
class TermLetterAdmin(ReadOnlyAdmin):
    # Review letters through the API so verification, dates and the audit log stay in step.
    list_display = ["visa", "status", "institution", "covers_from", "covers_to", "created_at"]
    list_filter = ["status"]
    exclude = ["file"]


@admin.register(RightToWorkCheck)
class RightToWorkCheckAdmin(ReadOnlyAdmin):
    list_display = ["employee", "visa", "outcome", "checked_on", "next_check_due", "checked_by"]
    list_filter = ["outcome", "check_type"]


@admin.register(VisaAuditLog)
class VisaAuditLogAdmin(ReadOnlyAdmin):
    list_display = ["created_at", "user", "action", "employee"]
    list_filter = ["action"]
