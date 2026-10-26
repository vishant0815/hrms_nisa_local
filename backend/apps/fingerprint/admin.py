from django.contrib import admin

from .models import BreakPeriod, ClockDevice, Enrolment, Punch


class ReadOnlyAdmin(admin.ModelAdmin):
    """Punches and breaks feed time records, so they change through the API only. Here: look only."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ClockDevice)
class ClockDeviceAdmin(ReadOnlyAdmin):
    list_display = ["name", "store", "serial", "is_active", "last_seen_at"]
    exclude = ["secret_hash"]


@admin.register(Enrolment)
class EnrolmentAdmin(ReadOnlyAdmin):
    list_display = ["employee", "device_user_id", "consented_at", "withdrawn_at"]


@admin.register(Punch)
class PunchAdmin(ReadOnlyAdmin):
    list_display = ["device", "device_user_id", "kind", "received_at", "outcome", "reason"]
    list_filter = ["outcome", "device"]


@admin.register(BreakPeriod)
class BreakPeriodAdmin(ReadOnlyAdmin):
    list_display = ["record", "started_at", "ended_at"]
