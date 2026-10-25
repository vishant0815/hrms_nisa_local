from django.contrib import admin

from .models import LeaveAllowance, LeaveRequest


@admin.register(LeaveAllowance)
class LeaveAllowanceAdmin(admin.ModelAdmin):
    list_display = ["employee", "year_start", "days", "set_by", "updated_at"]


@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    # Read-only: decisions go through the API so the rota and allowance checks are not skipped.
    list_display = ["employee", "kind", "start_date", "end_date", "status", "days_requested", "days_charged"]
    list_filter = ["kind", "status"]
    readonly_fields = [f.name for f in LeaveRequest._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
