from django.urls import path

from . import views

urlpatterns = [
    path("me/attendance/", views.MyAttendanceView.as_view(), name="my-attendance"),
    path("attendance/", views.AttendanceListView.as_view(), name="attendance-list"),
    path("attendance/manual/", views.ManualRecordView.as_view(), name="attendance-manual"),
    path("attendance/<int:pk>/", views.AttendanceDetailView.as_view(), name="attendance-detail"),
    path("attendance/<int:pk>/edits/", views.AttendanceEditsView.as_view(), name="attendance-edits"),
    path("attendance/<int:pk>/approve/", views.AttendanceDecisionView.as_view(approve=True), name="attendance-approve"),
    path("attendance/<int:pk>/reject/", views.AttendanceDecisionView.as_view(approve=False), name="attendance-reject"),
]
