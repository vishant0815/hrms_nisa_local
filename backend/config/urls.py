from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("apps.core.urls")),
    path("api/", include("apps.accounts.urls")),
    path("api/", include("apps.stores.urls")),
    path("api/", include("apps.employees.urls")),
    path("api/", include("apps.visa.urls")),
]
