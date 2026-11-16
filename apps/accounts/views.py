import uuid
from functools import cache

from django.contrib.auth import get_user, login, logout, update_session_auth_hash
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import status, viewsets
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import google_auth
from . import otp as otp_service
from .merge import merge_guest_into
from .models import Address, CustomerProfile, User
from .phone import normalise_phone
from .serializers import (
    AddressSerializer,
    GoogleLoginSerializer,
    OTPRequestSerializer,
    OTPVerifySerializer,
    PasswordLoginSerializer,
    SetPasswordSerializer,
    UserSerializer,
)


def _client_ip(request):
    fwd = request.META.get("HTTP_X_FORWARDED_FOR")
    return (fwd.split(",")[0].strip() if fwd else request.META.get("REMOTE_ADDR")) or None


class OTPRequestView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp_request"

    def post(self, request):
        ser = OTPRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        phone = normalise_phone(ser.validated_data["phone"])
        try:
            otp_service.issue_otp(phone, _client_ip(request))
        except otp_service.OTPRateLimited as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        except otp_service.OTPDeliveryError:
            return Response({"detail": "We could not send the code right now. Please try again shortly."},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        # Same response whether or not an account exists (no account enumeration).
        return Response({"detail": "If the number is valid, a code has been sent."})


def _guest_in_session(request):
    session_user = get_user(request._request)  # DRF auth is disabled on login views; read the session directly
    return session_user if session_user.is_authenticated and session_user.is_guest else None


def _finish_login(request, user, created, guest, error_detail):
    """Merge the guest's data, start the Django session and build the login response."""
    with transaction.atomic():
        if created or not hasattr(user, "profile"):
            CustomerProfile.objects.get_or_create(user=user)
        if user.is_active and guest:
            merge_guest_into(guest, user)
    if not user.is_active:
        return Response({"detail": error_detail}, status=status.HTTP_400_BAD_REQUEST)
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return Response({"user": UserSerializer(user).data, "is_new": created or not user.full_name})


def _login_verified_phone(request, phone):
    """Start the Django session for a phone number whose ownership was already proven by an OTP."""
    guest = _guest_in_session(request)
    user, created = User.objects.get_or_create(phone=phone)
    return _finish_login(request, user, created, guest, "Invalid or expired code.")


class OTPVerifyView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp_verify"

    def post(self, request):
        ser = OTPVerifySerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        phone = normalise_phone(ser.validated_data["phone"])
        if not otp_service.verify_otp(phone, ser.validated_data["code"]):
            return Response({"detail": "Invalid or expired code."}, status=status.HTTP_400_BAD_REQUEST)
        return _login_verified_phone(request, phone)


class GoogleLoginView(APIView):
    """Exchange a Google ID token (Sign in with Google) for a Django session; creates the account on first use."""

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "google_login"

    def post(self, request):
        ser = GoogleLoginSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            info = google_auth.verify_google_token(ser.validated_data["credential"])
        except google_auth.GoogleAuthError:
            return Response({"detail": "Google sign-in failed. Please try again."}, status=status.HTTP_400_BAD_REQUEST)
        guest = _guest_in_session(request)
        user, created = google_auth.get_or_create_google_user(info)
        return _finish_login(request, user, created, guest, "Google sign-in failed. Please try again.")


@cache
def _dummy_hash() -> str:
    return make_password("not-a-real-password")


class PasswordLoginView(APIView):
    """Email + password login for accounts created with Google (their email is Google-verified) that set a password."""

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_login"

    def post(self, request):
        ser = PasswordLoginSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        email, password = ser.validated_data["email"].strip(), ser.validated_data["password"]
        # Only Google-linked accounts: their email is verified and locked. A customer-typed profile email is never trusted.
        user = User.objects.filter(email__iexact=email, google_sub__isnull=False, is_guest=False, is_active=True).first()
        if user is None:
            check_password(password, _dummy_hash())  # same timing whether or not the email exists
        if user is None or not user.check_password(password):
            return Response({"detail": "Incorrect email or password."}, status=status.HTTP_400_BAD_REQUEST)
        return _finish_login(request, user, False, _guest_in_session(request), "Incorrect email or password.")


class SetPasswordView(APIView):
    """Set (or change) the password of a Google-linked account so it can also log in with email + password."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_set"

    def post(self, request):
        user = request.user
        if user.is_guest or not user.google_sub or not user.email:
            return Response({"detail": "Sign in with Google first to set a password."}, status=status.HTTP_400_BAD_REQUEST)
        ser = SetPasswordSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        if user.has_usable_password() and not user.check_password(ser.validated_data.get("current_password", "")):
            return Response({"current_password": ["Current password is incorrect."]}, status=status.HTTP_400_BAD_REQUEST)
        try:
            validate_password(ser.validated_data["new_password"], user)
        except DjangoValidationError as exc:
            return Response({"new_password": list(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)
        user.set_password(ser.validated_data["new_password"])
        user.save(update_fields=["password"])
        update_session_auth_hash(request, user)  # keep this session signed in
        return Response(UserSerializer(user).data)


@api_view(["POST"])
@permission_classes([AllowAny])
@authentication_classes([])
def guest_view(request):
    """Create an anonymous checkout identity so people can start a book without verifying a number first."""
    if request.user.is_authenticated:
        return Response(UserSerializer(request.user).data)
    guest = User.objects.create_user(f"guest:{uuid.uuid4().hex[:10]}", is_guest=True)
    CustomerProfile.objects.create(user=guest)
    login(request, guest, backend="django.contrib.auth.backends.ModelBackend")
    return Response(UserSerializer(guest).data, status=status.HTTP_201_CREATED)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def logout_view(request):
    logout(request)
    return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data)

    def patch(self, request):
        ser = UserSerializer(request.user, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data)

    def delete(self, request):
        """Account deletion request: deactivates and anonymises personal fields."""
        user = request.user
        logout(request)
        user.is_active = False
        user.full_name, user.email = "", ""
        user.google_sub = None
        user.phone = f"deleted:{user.id}"[:16]
        user.save()
        user.addresses.all().delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class AddressViewSet(viewsets.ModelViewSet):
    serializer_class = AddressSerializer
    pagination_class = None

    def get_queryset(self):
        return Address.objects.filter(user=self.request.user)

    def _unset_other_defaults(self, address):
        if address.is_default:
            Address.objects.filter(user=address.user).exclude(pk=address.pk).update(is_default=False)

    def perform_create(self, serializer):
        first = not Address.objects.filter(user=self.request.user).exists()
        address = serializer.save(user=self.request.user, **({"is_default": True} if first else {}))
        self._unset_other_defaults(address)

    def perform_update(self, serializer):
        self._unset_other_defaults(serializer.save())
