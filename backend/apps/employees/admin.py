from django.contrib import admin

from .models import EmployeeProfile, StoreAssignment


@admin.register(EmployeeProfile)
class EmployeeProfileAdmin(admin.ModelAdmin):
    list_display = ["employee_number", "user", "job_title", "start_date", "end_date"]
    search_fields = ["employee_number", "user__email", "user__first_name", "user__last_name"]
    readonly_fields = ["employee_number"]


@admin.register(StoreAssignment)
class StoreAssignmentAdmin(admin.ModelAdmin):
    list_display = ["employee", "store", "kind", "status", "valid_from", "valid_to"]
    list_filter = ["kind", "status", "store"]
