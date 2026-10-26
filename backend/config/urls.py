from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("apps.core.urls")),
    path("api/", include("apps.accounts.urls")),
    path("api/", include("apps.stores.urls")),
    path("api/", include("apps.employees.urls")),
    path("api/", include("apps.visa.urls")),
    path("api/", include("apps.rota.urls")),
    path("api/", include("apps.attendance.urls")),
    path("api/", include("apps.fingerprint.urls")),
    path("api/", include("apps.notifications.urls")),
    path("api/", include("apps.leave.urls")),
    path("api/", include("apps.payroll.urls")),
]
