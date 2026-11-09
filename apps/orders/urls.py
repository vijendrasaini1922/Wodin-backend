from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import OrderViewSet, TrackOrderView

router = DefaultRouter()
router.register("orders", OrderViewSet, basename="order")
urlpatterns = [path("orders/track/<str:token>/", TrackOrderView.as_view())] + router.urls
