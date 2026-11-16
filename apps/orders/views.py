from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Address
from apps.accounts.serializers import AddressSerializer

from . import services
from .models import Order
from .serializers import OrderAddressSerializer, OrderSerializer, build_timeline


class OrderViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = OrderSerializer

    def get_queryset(self):
        return (Order.objects.filter(user=self.request.user)
                .prefetch_related("items", "history"))

    @action(detail=True, methods=["post"])
    def address(self, request, pk=None):
        order = self.get_object()
        if order.status not in services.PRE_PAYMENT | {Order.Status.PAID}:
            raise ValidationError({"detail": "The delivery address can no longer be changed."})
        ser = OrderAddressSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        if data.get("address_id"):
            addr = Address.objects.filter(pk=data["address_id"], user=request.user).first()
            if not addr:
                raise ValidationError({"address_id": "Unknown address."})
            snapshot = AddressSerializer(addr).data
        else:
            snapshot = dict(data["address"])
            snapshot.pop("is_default", None)
        order.shipping_address = {k: str(v) for k, v in snapshot.items() if k != "id"}
        if "contact_email" in data:
            order.contact_email = data["contact_email"]
        if not order.contact_phone:  # Google-only account: the delivery contact number becomes the order contact
            order.contact_phone = order.shipping_address.get("phone", "")[:16]
        order.save(update_fields=["shipping_address", "contact_email", "contact_phone", "updated_at"])
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        order = services.cancel(self.get_object(), request.user)
        return Response(OrderSerializer(order).data)


class TrackOrderView(APIView):
    """Public, token-based tracking (works for guests; reveals no personal details)."""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def get(self, request, token):
        order = services.order_from_token(token)
        if not order:
            raise NotFound()
        data = {
            "number": order.number, "status": order.status, "status_label": order.get_status_display(),
            "payment_status": order.payment_status, "timeline": build_timeline(order),
            "created_at": order.created_at,
        }
        shipment = getattr(order, "shipment", None)
        if shipment:
            data["shipment"] = {"courier": shipment.courier, "tracking_number": shipment.tracking_number,
                                "tracking_url": shipment.tracking_url}
        return Response(data)
