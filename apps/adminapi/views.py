from django.conf import settings
from django.db.models import Count, Q
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Role, User
from apps.catalog.models import AddOn, Coupon, Paper, PricingSettings, Product, ProductVariant, Theme
from apps.core.permissions import IsAdminRole, IsBackoffice, IsOperations
from apps.notifications.models import AuditLog
from apps.orders.models import Order
from apps.orders.services import set_status
from apps.photos.models import Photo
from apps.photos.serializers import PhotoSerializer
from apps.photos.storage import get_storage

from . import metrics
from .serializers import (AdminAddOnSerializer, AdminCouponSerializer, AdminOrderListSerializer, AdminOrderSerializer,
                          AdminPaperSerializer, AdminProductSerializer, AdminThemeSerializer, AdminVariantSerializer,
                          CustomerSerializer, PricingSettingsSerializer, StaffUserSerializer)


class DashboardView(APIView):
    permission_classes = [IsBackoffice]

    def get(self, request):
        return Response(metrics.dashboard())


class AnalyticsView(APIView):
    permission_classes = [IsBackoffice]

    def get(self, request):
        try:
            days = max(1, min(int(request.query_params.get("days", 30)), 365))
        except ValueError:
            raise ValidationError({"days": "Must be a number."})
        return Response(metrics.analytics(days))


class AdminOrderViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsBackoffice]

    def get_serializer_class(self):
        return AdminOrderListSerializer if self.action == "list" else AdminOrderSerializer

    def get_queryset(self):
        qs = Order.objects.all().prefetch_related("items", "history", "payments__refunds")
        p = self.request.query_params
        if p.get("status"): qs = qs.filter(status=p["status"])
        if p.get("payment_status"): qs = qs.filter(payment_status=p["payment_status"])
        if p.get("q"):
            qs = qs.filter(Q(number__icontains=p["q"]) | Q(contact_phone__icontains=p["q"]) | Q(contact_name__icontains=p["q"]))
        return qs

    @action(detail=True, methods=["post"], permission_classes=[IsOperations])
    def cancel(self, request, pk=None):
        order = self.get_object()
        if order.payment_status != Order.PaymentStatus.UNPAID:
            raise ValidationError({"detail": "Paid orders must be refunded before cancelling."})
        set_status(order, Order.Status.CANCELLED, actor=request.user, note=request.data.get("note", "Cancelled by staff")[:300])
        return Response(AdminOrderSerializer(Order.objects.get(pk=order.pk)).data)

    @action(detail=True, methods=["get"])
    def photos(self, request, pk=None):
        """Secure staff access to a customer's photos (signed, short-lived URLs). Every access is audited."""
        from apps.notifications import audit
        order = self.get_object()
        item = order.items.first()
        photos = Photo.objects.filter(trip=item.project.trip, status=Photo.Status.READY) if item else Photo.objects.none()
        audit.log("photos.staff_access", actor=request.user, obj=order, count=photos.count())
        return Response(PhotoSerializer(photos, many=True).data)

    @action(detail=True, methods=["get"], url_path="original-urls")
    def original_urls(self, request, pk=None):
        from apps.notifications import audit
        order = self.get_object()
        item = order.items.first()
        storage, out = get_storage(), []
        for p in (Photo.objects.filter(trip=item.project.trip, status=Photo.Status.READY) if item else []):
            out.append({"id": str(p.id), "filename": p.original_filename,
                        "url": storage.download_url(p.original_key, settings.DOWNLOAD_URL_TTL_SECONDS)})
        audit.log("photos.staff_original_access", actor=request.user, obj=order, count=len(out))
        return Response(out)


class CustomerViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsBackoffice]
    serializer_class = CustomerSerializer

    def get_queryset(self):
        qs = User.objects.filter(role=Role.CUSTOMER, is_guest=False, is_active=True).annotate(orders_count=Count("orders"))
        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(Q(phone__icontains=q) | Q(full_name__icontains=q) | Q(email__icontains=q))
        return qs.order_by("-created_at")

    @action(detail=True, methods=["get"])
    def orders(self, request, pk=None):
        return Response(AdminOrderListSerializer(Order.objects.filter(user=self.get_object()), many=True).data)


class StaffUserViewSet(mixins.ListModelMixin, mixins.UpdateModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet):
    """Admin-only: create staff accounts and assign roles."""

    permission_classes = [IsAdminRole]
    serializer_class = StaffUserSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]
    pagination_class = None

    def get_queryset(self):
        return User.objects.exclude(role=Role.CUSTOMER).order_by("role", "phone")

    def create(self, request, *args, **kwargs):
        from apps.accounts.phone import normalise_phone
        role = request.data.get("role")
        if role not in {r for r in Role.values if r != Role.CUSTOMER}:
            raise ValidationError({"role": "Choose a staff role."})
        user, created = User.objects.get_or_create(phone=normalise_phone(request.data.get("phone")))
        user.role, user.full_name = role, request.data.get("full_name", user.full_name)
        user.is_staff = role == Role.ADMIN
        user.save()
        return Response(StaffUserSerializer(user).data, status=201 if created else 200)

    def perform_update(self, serializer):
        if serializer.instance == self.request.user and serializer.validated_data.get("role", serializer.instance.role) != Role.ADMIN:
            raise ValidationError({"role": "You cannot remove your own admin role."})
        user = serializer.save()
        user.is_staff = user.role == Role.ADMIN
        user.save(update_fields=["is_staff"])


class AuditLogView(APIView):
    permission_classes = [IsAdminRole]

    def get(self, request):
        qs = AuditLog.objects.all()
        for f in ("action", "object_type", "object_id"):
            if request.query_params.get(f):
                qs = qs.filter(**{f: request.query_params[f]})
        return Response([{"id": a.id, "action": a.action, "actor": str(a.actor) if a.actor else None, "object_type": a.object_type,
                          "object_id": a.object_id, "details": a.details, "at": a.created_at} for a in qs[:200]])


class _CatalogAdmin(viewsets.ModelViewSet):
    permission_classes = [IsAdminRole]
    pagination_class = None


class ProductAdminViewSet(_CatalogAdmin):
    queryset = Product.objects.prefetch_related("variants")
    serializer_class = AdminProductSerializer


class VariantAdminViewSet(_CatalogAdmin):
    queryset = ProductVariant.objects.all()
    serializer_class = AdminVariantSerializer


class PaperAdminViewSet(_CatalogAdmin):
    queryset = Paper.objects.all()
    serializer_class = AdminPaperSerializer


class ThemeAdminViewSet(_CatalogAdmin):
    queryset = Theme.objects.all()
    serializer_class = AdminThemeSerializer


class AddOnAdminViewSet(_CatalogAdmin):
    queryset = AddOn.objects.all()
    serializer_class = AdminAddOnSerializer


class CouponAdminViewSet(_CatalogAdmin):
    queryset = Coupon.objects.all().order_by("-id")
    serializer_class = AdminCouponSerializer


class PricingSettingsView(APIView):
    permission_classes = [IsAdminRole]

    def get(self, request):
        return Response(PricingSettingsSerializer(PricingSettings.load()).data)

    def patch(self, request):
        ser = PricingSettingsSerializer(PricingSettings.load(), data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data)
