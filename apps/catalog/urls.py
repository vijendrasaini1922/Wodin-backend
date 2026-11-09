from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("catalog/products", views.ProductViewSet, basename="product")

urlpatterns = [
    path("catalog/options/", views.OptionsView.as_view()),
    path("catalog/quote/", views.QuoteView.as_view()),
    path("catalog/coupon/validate/", views.CouponValidateView.as_view()),
    path("", include(router.urls)),
]
