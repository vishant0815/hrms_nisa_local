from django.urls import path

from .views import BrandingEditView, BrandingLogoView, BrandingView, HealthView

urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    path("branding/", BrandingView.as_view(), name="branding"),
    path("branding/logo/", BrandingLogoView.as_view(), name="branding-logo"),
    path("branding/edit/", BrandingEditView.as_view(), name="branding-edit"),
]
