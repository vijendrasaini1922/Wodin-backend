from rest_framework.routers import DefaultRouter

from .views import DesignerProjectViewSet, ProjectViewSet

router = DefaultRouter()
router.register("projects", ProjectViewSet, basename="project")
router.register("design/projects", DesignerProjectViewSet, basename="design-project")
urlpatterns = router.urls
