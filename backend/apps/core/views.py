from django.db import connection
from django.http import FileResponse, Http404
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsOwnerOrHR

from . import branding
from .models import SiteBranding


class HealthView(APIView):
    """Liveness/readiness probe, used by Docker and Kubernetes. No auth, no user data."""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def get(self, request):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        except Exception:
            return Response({"status": "unavailable"}, status=503)
        return Response({"status": "ok"})


def branding_data(site):
    return {
        "company_name": site.company_name,
        "has_logo": bool(site.logo),
        "version": int(site.updated_at.timestamp() * 1000),  # changes with every edit, so browsers fetch the new logo
    }


class BrandingView(APIView):
    """The name and whether there is a logo. Public: the sign-in page needs it before anyone is signed in."""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def get(self, request):
        return Response(branding_data(SiteBranding.current()))


class BrandingLogoView(APIView):
    """The logo image itself. Public, and cached by the browser until the logo changes."""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def get(self, request):
        site = SiteBranding.current()
        if not site.logo:
            raise Http404()
        try:
            handle = site.logo.open("rb")
        except FileNotFoundError:
            raise Http404()
        response = FileResponse(handle, content_type=site.logo_content_type or "application/octet-stream")
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "public, max-age=3600"
        response["Content-Security-Policy"] = "default-src 'none'"
        return response


class BrandingEditView(APIView):
    """Owner and HR change the name or the logo."""

    permission_classes = [IsOwnerOrHR]
    parser_classes = [MultiPartParser, FormParser, *APIView.parser_classes]

    def patch(self, request):
        site = branding.set_name(name=request.data.get("company_name"), by=request.user)
        return Response(branding_data(site))

    def post(self, request):
        upload = request.FILES.get("file")
        if upload is None:
            return Response({"file": ["Choose an image to upload."]}, status=status.HTTP_400_BAD_REQUEST)
        site = branding.set_logo(upload=upload, by=request.user)
        return Response(branding_data(site), status=status.HTTP_201_CREATED)

    def delete(self, request):
        site = branding.remove_logo(by=request.user)
        return Response(branding_data(site))
