from django.contrib import admin

from .models import RotaWeek, Shift, ShiftTemplate


@admin.register(ShiftTemplate)
class ShiftTemplateAdmin(admin.ModelAdmin):
    list_display = ["store", "name", "start_time", "end_time", "is_active"]
    list_filter = ["store", "is_active"]


@admin.register(RotaWeek)
class RotaWeekAdmin(admin.ModelAdmin):
    list_display = ["store", "week_start", "status", "published_at"]
    list_filter = ["store", "status"]


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    # Edit shifts through the API: the admin would skip the right-to-work and hours checks.
    list_display = ["rota_week", "date", "start_time", "end_time", "employee", "status"]
    list_filter = ["status", "rota_week__store"]
    readonly_fields = [
        "rota_week", "employee", "date", "start_time", "end_time", "break_minutes", "status",
    ]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
