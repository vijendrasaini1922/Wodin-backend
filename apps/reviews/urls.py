from rest_framework.routers import DefaultRouter

from .views import AdminReviewViewSet, MyReviewViewSet, PublicReviewViewSet

router = DefaultRouter()
router.register("reviews", PublicReviewViewSet, basename="review")
router.register("my/reviews", MyReviewViewSet, basename="my-review")
router.register("ops/reviews", AdminReviewViewSet, basename="admin-review")
urlpatterns = router.urls
