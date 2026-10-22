from django.db.models import Prefetch
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.exceptions import NotFound
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Role
from apps.accounts.permissions import IsManagerOrAbove, IsOwnerOrHR
from apps.employees.models import EmployeeProfile
from apps.employees.scoping import employees_visible_to

from . import letters, services
from .models import EmployeeVisa, LetterStatus, RightToWorkCheck, TermLetter, VisaType
from .rules import check_right_to_work
from .serializers import (
    CanWorkQuerySerializer,
    EmployeeVisaCreateSerializer,
    EmployeeVisaSerializer,
    EmployeeVisaUpdateSerializer,
    MyTermLetterSerializer,
    RejectLetterSerializer,
    RevokeSerializer,
    RightToWorkCheckSerializer,
    TermLetterSerializer,
    VacationPeriodSerializer,
    VerifyLetterSerializer,
    VisaTypeSerializer,
)

# Every immigration endpoint below is Owner/HR only, except can-work which gives managers a
# yes/no. Managers and employees never see visa types, dates or documents.


def with_checks(queryset):
    """Load each visa's checks once, newest first, so status needs no per-row query."""
    return queryset.select_related("visa_type", "employee__user").prefetch_related(
        Prefetch(
            "checks",
            queryset=RightToWorkCheck.objects.order_by("-checked_on", "-id"),
            to_attr="checks_desc",
        )
    )


def get_employee(employee_id):
    return get_object_or_404(EmployeeProfile.objects.select_related("user"), pk=employee_id)


def get_visa(pk):
    return get_object_or_404(with_checks(EmployeeVisa.objects.all()), pk=pk)


class VisaTypeViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """The editable rules table. Types are switched off (is_active), never deleted."""

    queryset = VisaType.objects.all()
    serializer_class = VisaTypeSerializer
    permission_classes = [IsOwnerOrHR]
    http_method_names = ["get", "post", "patch", "head", "options"]


class EmployeeVisasView(APIView):
    permission_classes = [IsOwnerOrHR]

    def get(self, request, employee_id):
        employee = get_employee(employee_id)
        visas = with_checks(EmployeeVisa.objects.filter(employee=employee))
        services.audit(request.user, "visa_viewed", employee, detail="history")
        return Response(EmployeeVisaSerializer(visas, many=True).data)

    def post(self, request, employee_id):
        employee = get_employee(employee_id)
        serializer = EmployeeVisaCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        visa = services.add_visa(employee=employee, by=request.user, **serializer.validated_data)
        return Response(EmployeeVisaSerializer(get_visa(visa.pk)).data, status=status.HTTP_201_CREATED)


class VisaDetailView(APIView):
    permission_classes = [IsOwnerOrHR]

    def get(self, request, pk):
        visa = get_visa(pk)
        services.audit(request.user, "visa_viewed", visa.employee, visa, "detail")
        return Response(EmployeeVisaSerializer(visa).data)

    def patch(self, request, pk):
        visa = get_visa(pk)
        serializer = EmployeeVisaUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.update_visa(visa=visa, by=request.user, **serializer.validated_data)
        return Response(EmployeeVisaSerializer(get_visa(pk)).data)


class VisaRevokeView(APIView):
    permission_classes = [IsOwnerOrHR]

    def post(self, request, pk):
        visa = get_visa(pk)
        body = RevokeSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        services.revoke_visa(visa=visa, by=request.user, reason=body.validated_data["reason"])
        return Response(EmployeeVisaSerializer(get_visa(pk)).data)


class VisaVacationsView(APIView):
    """Vacation periods, read-only. They are created by verifying a student's term letter."""

    permission_classes = [IsOwnerOrHR]

    def get(self, request, pk):
        visa = get_visa(pk)
        return Response(VacationPeriodSerializer(visa.vacations.all(), many=True).data)


class MyTermLettersView(APIView):
    """A student's own letters: list them, or upload a new one (multipart: file, institution)."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    throttle_scope = "upload"

    def get_throttles(self):
        return super().get_throttles() if self.request.method == "POST" else []

    def get_profile(self, request):
        profile = EmployeeProfile.objects.filter(user=request.user).first()
        if profile is None:
            raise NotFound("You do not have an employee profile.")
        return profile

    def get(self, request):
        profile = self.get_profile(request)
        mine = TermLetter.objects.filter(visa__employee=profile)
        return Response(MyTermLetterSerializer(mine, many=True).data)

    def post(self, request):
        profile = self.get_profile(request)
        upload = request.FILES.get("file")
        if upload is None:
            return Response({"file": ["Choose a file to upload."]}, status=status.HTTP_400_BAD_REQUEST)
        letter = letters.upload_letter(
            employee=profile, user=request.user, upload=upload,
            institution=str(request.data.get("institution", "")),
        )
        return Response(MyTermLetterSerializer(letter).data, status=status.HTTP_201_CREATED)


class TermLetterQueueView(APIView):
    """HR's list of letters. Defaults to the ones waiting to be checked."""

    permission_classes = [IsOwnerOrHR]

    def get(self, request):
        wanted = request.query_params.get("status", LetterStatus.PENDING)
        queryset = TermLetter.objects.select_related("visa__employee__user").prefetch_related("vacations")
        if wanted != "all":
            if wanted not in LetterStatus.values:
                return Response({"status": "Use pending, verified, rejected or all."}, status=400)
            queryset = queryset.filter(status=wanted)
        return Response(TermLetterSerializer(queryset.order_by("created_at", "id"), many=True).data)


class EmployeeTermLettersView(APIView):
    permission_classes = [IsOwnerOrHR]

    def get(self, request, employee_id):
        employee = get_employee(employee_id)
        queryset = (
            TermLetter.objects.filter(visa__employee=employee)
            .select_related("visa__employee__user")
            .prefetch_related("vacations")
        )
        return Response(TermLetterSerializer(queryset, many=True).data)


def get_letter(pk):
    return get_object_or_404(
        TermLetter.objects.select_related("visa__employee__user").prefetch_related("vacations"), pk=pk
    )


class TermLetterFileView(APIView):
    """Download the uploaded file. Owner/HR only, and every download is logged."""

    permission_classes = [IsOwnerOrHR]

    def get(self, request, pk):
        letter = get_letter(pk)
        try:
            handle = letter.file.open("rb")
        except FileNotFoundError:
            raise NotFound("The file is missing from storage.")
        services.audit(request.user, "term_letter_downloaded", letter.visa.employee, letter.visa)
        extension = letters.FILE_TYPES[letter.content_type][0]
        response = FileResponse(
            handle, as_attachment=True, filename=f"term-letter-{letter.pk}{extension}",
            content_type=letter.content_type,
        )
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "private, no-store"
        return response


class TermLetterVerifyView(APIView):
    permission_classes = [IsOwnerOrHR]

    def post(self, request, pk):
        letter = get_letter(pk)
        body = VerifyLetterSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        letters.verify_letter(letter=letter, by=request.user, **body.validated_data)
        return Response(TermLetterSerializer(get_letter(pk)).data)


class TermLetterRejectView(APIView):
    permission_classes = [IsOwnerOrHR]

    def post(self, request, pk):
        letter = get_letter(pk)
        body = RejectLetterSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        letters.reject_letter(letter=letter, by=request.user, reason=body.validated_data["reason"])
        return Response(TermLetterSerializer(get_letter(pk)).data)


class MissingTermLettersView(APIView):
    """Students with no verified term letter covering today: the people HR needs to chase."""

    permission_classes = [IsOwnerOrHR]

    def get(self, request):
        visas = with_checks(letters.students_missing_letters())
        return Response(EmployeeVisaSerializer(visas, many=True).data)


class EmployeeChecksView(APIView):
    """The right-to-work audit trail. Add-only: a correction is a new check."""

    permission_classes = [IsOwnerOrHR]

    def get(self, request, employee_id):
        employee = get_employee(employee_id)
        return Response(RightToWorkCheckSerializer(employee.rtw_checks.all(), many=True).data)

    def post(self, request, employee_id):
        employee = get_employee(employee_id)
        serializer = RightToWorkCheckSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        data.setdefault("checked_on", timezone.localdate())
        check = services.record_check(employee=employee, by=request.user, **data)
        return Response(RightToWorkCheckSerializer(check).data, status=status.HTTP_201_CREATED)


class ExpiringVisasView(APIView):
    """HR dashboard: statuses expiring soon, plus any that have already expired."""

    permission_classes = [IsOwnerOrHR]

    def get(self, request):
        try:
            days = int(request.query_params.get("days", 60))
        except ValueError:
            return Response({"days": "Enter a whole number of days."}, status=status.HTTP_400_BAD_REQUEST)
        if not 1 <= days <= 365:
            return Response({"days": "Use a number between 1 and 365."}, status=status.HTTP_400_BAD_REQUEST)
        visas = with_checks(services.expiring_visas(days))
        return Response(EmployeeVisaSerializer(visas, many=True).data)


class CanWorkView(APIView):
    """Can this employee work on a date? Owner/HR get the detail; managers get yes/no and the hours limit."""

    permission_classes = [IsManagerOrAbove]

    def get(self, request, employee_id):
        employee = employees_visible_to(request.user).filter(pk=employee_id).first()
        if employee is None:
            raise NotFound()
        query = CanWorkQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        on = query.validated_data.get("date") or timezone.localdate()
        decision = check_right_to_work(employee, on, query.validated_data.get("week_hours"))

        if request.user.role in (Role.OWNER, Role.HR):
            body = decision.for_hr()
        else:
            body = decision.for_manager()
        return Response({"employee": employee.pk, "date": on, **body})
