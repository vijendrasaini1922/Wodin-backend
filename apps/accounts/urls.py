from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("addresses", views.AddressViewSet, basename="address")

urlpatterns = [
    path("auth/otp/request/", views.OTPRequestView.as_view()),
    path("auth/otp/verify/", views.OTPVerifyView.as_view()),
    path("auth/google/", views.GoogleLoginView.as_view()),
    path("auth/password/", views.PasswordLoginView.as_view()),
    path("auth/password/set/", views.SetPasswordView.as_view()),
    path("auth/guest/", views.guest_view),
    path("auth/logout/", views.logout_view),
    path("me/", views.MeView.as_view()),
    path("", include(router.urls)),
]
