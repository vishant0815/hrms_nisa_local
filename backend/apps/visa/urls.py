from django.urls import path
from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter()
router.register("visa-types", views.VisaTypeViewSet, basename="visa-type")

urlpatterns = [
    path("employees/<int:employee_id>/visas/", views.EmployeeVisasView.as_view(), name="employee-visas"),
    path("employees/<int:employee_id>/rtw-checks/", views.EmployeeChecksView.as_view(), name="employee-rtw-checks"),
    path("employees/<int:employee_id>/can-work/", views.CanWorkView.as_view(), name="employee-can-work"),
    path("visas/expiring/", views.ExpiringVisasView.as_view(), name="visa-expiring"),
    path("visas/<int:pk>/", views.VisaDetailView.as_view(), name="visa-detail"),
    path("visas/<int:pk>/revoke/", views.VisaRevokeView.as_view(), name="visa-revoke"),
    path("visas/<int:pk>/vacations/", views.VisaVacationsView.as_view(), name="visa-vacations"),
    *router.urls,
]
