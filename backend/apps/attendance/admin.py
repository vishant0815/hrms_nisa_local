from django.contrib import admin

from .models import AttendanceEdit, AttendanceRecord


class ReadOnlyAdmin(admin.ModelAdmin):
    """Time records are changed through the API so every change is audited. Here: look only."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(ReadOnlyAdmin):
    list_display = ["employee", "store", "clock_in_at", "clock_out_at", "review_status", "source"]
    list_filter = ["store", "review_status", "source"]
    exclude = ["in_latitude", "in_longitude", "out_latitude", "out_longitude"]


@admin.register(AttendanceEdit)
class AttendanceEditAdmin(ReadOnlyAdmin):
    list_display = ["record", "edited_by", "reason", "created_at"]
