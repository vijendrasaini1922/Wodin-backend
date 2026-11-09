import uuid

from django.contrib.auth import get_user, login, logout
from django.db import transaction
from rest_framework import status, viewsets
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import otp as otp_service
from .merge import merge_guest_into
from .models import Address, CustomerProfile, User
from .phone import normalise_phone
from .serializers import AddressSerializer, OTPRequestSerializer, OTPVerifySerializer, UserSerializer


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
        session_user = get_user(request._request)  # DRF auth is disabled here; read the session directly
        guest = session_user if session_user.is_authenticated and session_user.is_guest else None
        with transaction.atomic():
            user, created = User.objects.get_or_create(phone=phone)
            if created:
                CustomerProfile.objects.create(user=user)
            if user.is_active and guest:
                merge_guest_into(guest, user)
        if not user.is_active:
            return Response({"detail": "Invalid or expired code."}, status=status.HTTP_400_BAD_REQUEST)
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        return Response({"user": UserSerializer(user).data, "is_new": created or not user.full_name})


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
