from django.http import HttpResponse
from rest_framework.exceptions import NotFound
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
            from rest_framework.exceptions import ValidationError
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
