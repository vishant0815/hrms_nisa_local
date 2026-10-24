from django.urls import path

from . import views

urlpatterns = [
    path("notifications/status/", views.NotificationStatusView.as_view(), name="notification-status"),
]
