from django.urls import path
from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter()
router.register("rota-weeks", views.RotaWeekViewSet, basename="rota-week")
router.register("shifts", views.ShiftViewSet, basename="shift")
router.register("shift-templates", views.ShiftTemplateViewSet, basename="shift-template")

urlpatterns = [
    path("me/rota/", views.MyRotaView.as_view(), name="my-rota"),
    *router.urls,
]
