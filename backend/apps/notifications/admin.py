from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["kind", "recipient", "status", "attempts", "created_at", "sent_at"]
    list_filter = ["status", "kind"]
    exclude = ["context"]  # the email's contents are not needed to look into delivery problems

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
