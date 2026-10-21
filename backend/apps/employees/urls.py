from rest_framework.routers import SimpleRouter

from .views import CoverRequestViewSet, EmployeeViewSet

router = SimpleRouter()
router.register("employees", EmployeeViewSet, basename="employee")
router.register("cover-requests", CoverRequestViewSet, basename="cover-request")

urlpatterns = router.urls
