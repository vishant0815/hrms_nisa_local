from django.urls import path

from . import views

urlpatterns = [
    path("me/leave/", views.MyLeaveView.as_view(), name="my-leave"),
    path("me/leave/balance/", views.MyLeaveBalanceView.as_view(), name="my-leave-balance"),
    path("me/leave/<int:pk>/cancel/", views.MyLeaveCancelView.as_view(), name="my-leave-cancel"),
    path("leave/", views.LeaveQueueView.as_view(), name="leave-queue"),
    path("leave/calendar/", views.LeaveCalendarView.as_view(), name="leave-calendar"),
    path("leave/<int:pk>/approve/", views.LeaveDecisionView.as_view(action="approve"), name="leave-approve"),
    path("leave/<int:pk>/reject/", views.LeaveDecisionView.as_view(action="reject"), name="leave-reject"),
    path("leave/<int:pk>/cancel/", views.LeaveDecisionView.as_view(action="cancel"), name="leave-cancel"),
    path("employees/<int:employee_id>/leave-allowance/", views.EmployeeLeaveAllowanceView.as_view(),
         name="employee-leave-allowance"),
]
