from rest_framework.routers import SimpleRouter

from .views import StoreViewSet

router = SimpleRouter()
router.register("stores", StoreViewSet, basename="store")

urlpatterns = router.urls
