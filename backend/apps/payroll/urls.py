from django.urls import path

from . import views

urlpatterns = [
    path("payslip-template/", views.PayslipTemplateView.as_view(), name="payslip-template"),
    path("payslip-template/logo/", views.PayslipLogoView.as_view(), name="payslip-template-logo"),
    path("employees/<int:employee_id>/pay-rates/", views.EmployeeRatesView.as_view(), name="employee-pay-rates"),
    path("employees/<int:employee_id>/pay-details/", views.EmployeePayDetailsView.as_view(), name="employee-pay-details"),
    path("pay-runs/", views.PayRunListView.as_view(), name="pay-runs"),
    path("pay-runs/<int:pk>/", views.PayRunDetailView.as_view(), name="pay-run"),
    path("pay-runs/<int:pk>/rebuild/", views.PayRunRebuildView.as_view(), name="pay-run-rebuild"),
    path("pay-runs/<int:pk>/approve/", views.PayRunApproveView.as_view(), name="pay-run-approve"),
    path("payslips/<int:pk>/", views.PayslipDetailView.as_view(), name="payslip"),
    path("payslips/<int:pk>/deductions/", views.PayslipDeductionsView.as_view(), name="payslip-deductions"),
    path("attendance/<int:pk>/approve-extra-hours/", views.ExtraHoursView.as_view(), name="approve-extra-hours"),
    path("payslips/<int:pk>/suggest/", views.PayslipSuggestView.as_view(), name="payslip-suggest"),
    path("payslips/<int:pk>/sick-pay/", views.PayslipSickPayView.as_view(), name="payslip-sick-pay"),
    path("payslips/<int:pk>/pdf/", views.PayslipPdfView.as_view(), name="payslip-pdf"),
    path("pay-runs/<int:pk>/bank-file/", views.PayRunBankFileView.as_view(), name="pay-run-bank-file"),
    path("me/payslips/<int:pk>/pdf/", views.MyPayslipPdfView.as_view(), name="my-payslip-pdf"),
    path("me/payslips/", views.MyPayslipsView.as_view(), name="my-payslips"),
    path("me/payslips/<int:pk>/", views.MyPayslipDetailView.as_view(), name="my-payslip"),
]
