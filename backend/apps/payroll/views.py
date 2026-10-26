from django.http import HttpResponse
from rest_framework.exceptions import NotFound
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsOwnerOrHR

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
