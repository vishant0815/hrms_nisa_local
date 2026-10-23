from django.urls import path
from rest_framework.routers import SimpleRouter

from . import swap_views, views

router = SimpleRouter()
router.register("rota-weeks", views.RotaWeekViewSet, basename="rota-week")
router.register("shifts", views.ShiftViewSet, basename="shift")
router.register("shift-templates", views.ShiftTemplateViewSet, basename="shift-template")

urlpatterns = [
    path("me/rota/", views.MyRotaView.as_view(), name="my-rota"),
    path("me/swaps/", swap_views.MySwapsView.as_view(), name="my-swaps"),
    path("me/swaps/colleagues/", swap_views.MySwapColleaguesView.as_view(), name="my-swap-colleagues"),
    path("me/swaps/<int:pk>/accept/", swap_views.MySwapActionView.as_view(action="accept"), name="my-swap-accept"),
    path("me/swaps/<int:pk>/decline/", swap_views.MySwapActionView.as_view(action="decline"), name="my-swap-decline"),
    path("me/swaps/<int:pk>/cancel/", swap_views.MySwapActionView.as_view(action="cancel"), name="my-swap-cancel"),
    path("swaps/", swap_views.SwapQueueView.as_view(), name="swap-queue"),
    path("swaps/<int:pk>/approve/", swap_views.SwapDecisionView.as_view(action="approve"), name="swap-approve"),
    path("swaps/<int:pk>/reject/", swap_views.SwapDecisionView.as_view(action="reject"), name="swap-reject"),
    *router.urls,
]
