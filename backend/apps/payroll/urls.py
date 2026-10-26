from django.urls import path

from . import views

urlpatterns = [
    path("payslip-template/", views.PayslipTemplateView.as_view(), name="payslip-template"),
    path("payslip-template/logo/", views.PayslipLogoView.as_view(), name="payslip-template-logo"),
]
