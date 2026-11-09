from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("photos", views.PhotoViewSet, basename="photo")

urlpatterns = [
    path("trips/<uuid:trip_id>/photos/", views.TripPhotosView.as_view()),
    path("trips/<uuid:trip_id>/photos/upload-urls/", views.UploadUrlsView.as_view()),
    path("trips/<uuid:trip_id>/photo-groups/", views.PhotoGroupViewSet.as_view({"get": "list", "post": "create"})),
    path("trips/<uuid:trip_id>/photo-groups/<uuid:pk>/",
         views.PhotoGroupViewSet.as_view({"patch": "partial_update", "delete": "destroy"})),
    path("photos/<uuid:photo_id>/complete/", views.CompleteUploadView.as_view()),
    path("storage/upload/<str:token>/", views.local_upload),
    path("storage/download/<str:token>/", views.local_download),
    path("", include(router.urls)),
]
