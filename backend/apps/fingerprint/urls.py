from django.urls import path

from . import views

urlpatterns = [
    path("clock-devices/", views.DeviceListView.as_view(), name="clock-devices"),
    path("clock-devices/<int:pk>/", views.DeviceDetailView.as_view(), name="clock-device-detail"),
    path("clock-devices/<int:pk>/rotate-secret/", views.DeviceSecretView.as_view(), name="clock-device-secret"),
    path("fingerprint-enrolments/", views.EnrolmentListView.as_view(), name="fingerprint-enrolments"),
    path("fingerprint-enrolments/<int:pk>/withdraw/", views.EnrolmentWithdrawView.as_view(), name="fingerprint-withdraw"),
    path("clock-punches/", views.PunchListView.as_view(), name="clock-punches"),
    path("attendance/breaks/", views.BreakReportView.as_view(), name="break-report"),
    path("attendance/<int:pk>/break-basis/", views.BreakBasisView.as_view(), name="break-basis"),
]
