from django.http import HttpResponse
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsManagerOrAbove, IsOwnerOrHR

from . import services
from .models import PayslipTemplate
from .serializers import PayslipTemplateSerializer


class PayslipTemplateView(APIView):
    permission_classes = [IsOwnerOrHR]

    def get(self, request):
        return Response(PayslipTemplateSerializer(PayslipTemplate.current()).data)

    def patch(self, request):
        serializer = PayslipTemplateSerializer(PayslipTemplate.current(), data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        template = services.update_template(request.user, serializer.validated_data)
        return Response(PayslipTemplateSerializer(template).data)


class PayslipLogoView(APIView):
    permission_classes = [IsOwnerOrHR]
    parser_classes = [MultiPartParser]

    def get(self, request):
        template = PayslipTemplate.current()
        if not template.logo:
            raise NotFound("No logo has been uploaded yet.")
        try:
            with template.logo.open("rb") as handle:
                raw = handle.read()
        except FileNotFoundError:
            raise NotFound("The logo is missing from storage.")
        content_type, _ = services.check_logo(raw)
        response = HttpResponse(raw, content_type=content_type)
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "private, no-store"
        return response

    def post(self, request):
        upload = request.FILES.get("logo")
        if upload is None:
            raise ValidationError({"logo": "Attach the logo file in a field called 'logo'."})
        template, _ = services.set_logo(request.user, upload.read())
        return Response(PayslipTemplateSerializer(template).data, status=201)

    def delete(self, request):
        template = services.clear_logo(request.user)
        return Response(PayslipTemplateSerializer(template).data)


# --- Rates and pay details -----------------------------------------------------------------------

from django.shortcuts import get_object_or_404

from apps.employees.models import EmployeeProfile

from .models import PayDetails, PayRate, PayRun, Payslip, RunStatus
from .serializers import (
    CreateRunInput,
    DeductionsInput,
    PayDetailsInput,
    PayRunSerializer,
    PayslipSerializer,
    RateSerializer,
    SickPayInput,
    mask_details,
)


class EmployeeRatesView(APIView):
    permission_classes = [IsOwnerOrHR]

    def get(self, request, employee_id):
        employee = get_object_or_404(EmployeeProfile, pk=employee_id)
        return Response(RateSerializer(PayRate.objects.filter(employee=employee), many=True).data)

    def post(self, request, employee_id):
        employee = get_object_or_404(EmployeeProfile, pk=employee_id)
        serializer = RateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        rate = services.add_rate(request.user, employee, **serializer.validated_data)
        return Response(RateSerializer(rate).data, status=201)


class EmployeePayDetailsView(APIView):
    permission_classes = [IsOwnerOrHR]

    def get(self, request, employee_id):
        employee = get_object_or_404(EmployeeProfile, pk=employee_id)
        return Response(mask_details(PayDetails.objects.filter(employee=employee).first()))

    def put(self, request, employee_id):
        employee = get_object_or_404(EmployeeProfile, pk=employee_id)
        serializer = PayDetailsInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        details = services.set_pay_details(request.user, employee, serializer.validated_data)
        return Response(mask_details(details))


# --- Pay runs -----------------------------------------------------------------------------------

def run_detail(run):
    data = PayRunSerializer(run).data
    slips = run.payslips.select_related("employee__user")
    data["payslips"] = PayslipSerializer(slips, many=True).data
    data["problems"] = services.blockers(run) if run.status == RunStatus.DRAFT else []
    return data


class PayRunListView(APIView):
    permission_classes = [IsOwnerOrHR]

    def get(self, request):
        return Response(PayRunSerializer(PayRun.objects.all()[:60], many=True).data)

    def post(self, request):
        serializer = CreateRunInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        run = services.create_pay_run(request.user, **serializer.validated_data)
        return Response(run_detail(run), status=201)


class PayRunDetailView(APIView):
    permission_classes = [IsOwnerOrHR]

    def get(self, request, pk):
        return Response(run_detail(get_object_or_404(PayRun, pk=pk)))


class PayRunRebuildView(APIView):
    permission_classes = [IsOwnerOrHR]

    def post(self, request, pk):
        run = services.fill_run(get_object_or_404(PayRun, pk=pk))
        return Response(run_detail(run))


class PayRunApproveView(APIView):
    permission_classes = [IsOwnerOrHR]

    def post(self, request, pk):
        run = services.approve_run(request.user, get_object_or_404(PayRun, pk=pk), PayslipTemplate.current())
        return Response(run_detail(run))


class PayslipDetailView(APIView):
    permission_classes = [IsOwnerOrHR]

    def get(self, request, pk):
        return Response(PayslipSerializer(get_object_or_404(Payslip, pk=pk)).data)


class PayslipDeductionsView(APIView):
    permission_classes = [IsOwnerOrHR]

    def patch(self, request, pk):
        payslip = get_object_or_404(Payslip, pk=pk)
        serializer = DeductionsInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        confirmed = data.pop("confirmed", None)
        payslip = services.set_deductions(request.user, payslip, data, confirmed)
        return Response(PayslipSerializer(payslip).data)


class PayslipSuggestView(APIView):
    """Work out tax, National Insurance, student loan and pension again from the person's current pay details."""

    permission_classes = [IsOwnerOrHR]

    def post(self, request, pk):
        payslip = get_object_or_404(Payslip, pk=pk)
        return Response(PayslipSerializer(services.suggest_deductions(request.user, payslip)).data)


class PayslipSickPayView(APIView):
    """Statutory sick pay as worked out by the accountant, for someone who was off sick this week."""

    permission_classes = [IsOwnerOrHR]

    def patch(self, request, pk):
        payslip = get_object_or_404(Payslip, pk=pk)
        serializer = SickPayInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        payslip = services.set_sick_pay(request.user, payslip, serializer.validated_data["sick_pay"])
        return Response(PayslipSerializer(payslip).data)


class ExtraHoursView(APIView):
    """A manager agrees that time clocked beyond the rota should be paid."""

    permission_classes = [IsManagerOrAbove]

    def post(self, request, pk):
        from apps.attendance.scoping import attendance_visible_to

        record = get_object_or_404(attendance_visible_to(request.user), pk=pk)
        note = str(request.data.get("note", ""))
        services.approve_extra_hours(request.user, record, note)
        return Response({"record": record.pk, "approved": True}, status=201)


# --- An employee's own payslips -----------------------------------------------------------------

class MyPayslipsView(APIView):
    def get(self, request):
        slips = my_payslips(request)
        return Response(PayslipSerializer(slips, many=True).data)


class MyPayslipDetailView(APIView):
    def get(self, request, pk):
        return Response(PayslipSerializer(get_object_or_404(my_payslips(request), pk=pk)).data)


def my_payslips(request):
    profile = getattr(request.user, "employee_profile", None)
    if profile is None:
        return Payslip.objects.none()
    return Payslip.objects.filter(
        employee=profile, pay_run__status__in=[RunStatus.APPROVED, RunStatus.SENT]
    ).select_related("employee__user").order_by("-pay_run__period_start")


# --- PDFs and the bank file -----------------------------------------------------------------------

import csv
from io import StringIO

from django.http import FileResponse


def pdf_response(slip):
    if not slip.pdf:
        raise NotFound("This payslip has no PDF yet.")
    try:
        handle = slip.pdf.open("rb")
    except FileNotFoundError:
        raise NotFound("The PDF is missing from storage.")
    response = FileResponse(handle, as_attachment=False, filename=f"payslip-{slip.number}.pdf", content_type="application/pdf")
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response


class PayslipPdfView(APIView):
    permission_classes = [IsOwnerOrHR]

    def get(self, request, pk):
        return pdf_response(get_object_or_404(Payslip, pk=pk))


class MyPayslipPdfView(APIView):
    def get(self, request, pk):
        return pdf_response(get_object_or_404(my_payslips(request), pk=pk))


def spreadsheet_safe(value):
    """A cell that starts with = + - @ can be run as a formula by Excel, so it is defanged."""
    value = str(value)
    return "'" + value if value[:1] in "=+-@\t\r" else value


class PayRunBankFileView(APIView):
    """The payments to upload to the bank. Nothing here moves money."""

    permission_classes = [IsOwnerOrHR]

    def get(self, request, pk):
        run = get_object_or_404(PayRun, pk=pk)
        if run.status == RunStatus.DRAFT:
            raise ValidationError({"status": "Approve the pay run before making the bank file."})
        rows, missing = services.bank_rows(run)
        if missing:
            raise ValidationError({"bank_details": f"These people have no bank details yet: {', '.join(missing)}."})
        out = StringIO()
        writer = csv.writer(out)
        writer.writerow(["Name", "Sort code", "Account number", "Amount", "Reference"])
        for row in rows:
            writer.writerow([spreadsheet_safe(row["name"]), row["sort_code"], row["account_number"], row["amount"], row["reference"]])
        response = HttpResponse(out.getvalue(), content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="bank-payments-{run.pay_date.isoformat()}.csv"'
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response
