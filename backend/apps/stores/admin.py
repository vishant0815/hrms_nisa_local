from django.contrib import admin

from .models import Store, StoreHours


class StoreHoursInline(admin.TabularInline):
    model = StoreHours
    extra = 0


@admin.register(Store)
class StoreAdmin(admin.ModelAdmin):
    list_display = ["name", "phone", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["name", "address"]
    filter_horizontal = ["managers"]
    inlines = [StoreHoursInline]
