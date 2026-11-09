from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Role
from apps.core.permissions import IsOperations
from apps.orders.models import Order
from apps.orders.services import order_from_token

from . import services
from .models import Shipment
from .providers import get_provider
from .serializers import DispatchSerializer, EventCreateSerializer, EventSerializer, ShipmentSerializer


class ServiceabilityView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []

    def get(self, request):
        pincode = request.query_params.get("pincode", "")
        return Response({"pincode": pincode, "serviceable": get_provider().serviceable(pincode)})


class ShipmentListDispatchView(APIView):
    permission_classes = [IsOperations]

    def get(self, request):
        return Response(ShipmentSerializer(Shipment.objects.select_related("order").prefetch_related("events"), many=True).data)

    def post(self, request):
        ser = DispatchSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        order = Order.objects.filter(pk=d["order_id"]).first()
        if not order:
            raise ValidationError({"order_id": "Unknown order."})
        pincode = (order.shipping_address or {}).get("pincode", "")
        if not get_provider().serviceable(pincode):
            raise ValidationError({"detail": f"PIN code {pincode or '(none)'} is not serviceable."})
        shipment = services.dispatch(order, d["courier"], d["tracking_number"], d["tracking_url"], request.user)
        return Response(ShipmentSerializer(shipment).data, status=status.HTTP_201_CREATED)


class _ShipmentBase(APIView):
    permission_classes = [IsOperations]

    def shipment(self, pk) -> Shipment:
        s = Shipment.objects.filter(pk=pk).first()
        if not s:
            raise NotFound()
        return s


class ShipmentEventView(_ShipmentBase):
    def post(self, request, pk):
        ser = EventCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        event = services.add_event(self.shipment(pk), **{"location": "", "note": "", **ser.validated_data})
        return Response(EventSerializer(event).data, status=status.HTTP_201_CREATED)


class ShipmentDeliveredView(_ShipmentBase):
    def post(self, request, pk):
        return Response(ShipmentSerializer(services.mark_delivered(self.shipment(pk), request.user)).data)


class MyShipmentView(APIView):
    """Customer: courier details and milestones for their own order."""

    def get(self, request, order_id):
        order = Order.objects.filter(pk=order_id, user=request.user).first()
        shipment = getattr(order, "shipment", None) if order else None
        if not shipment:
            raise NotFound()
        return Response(ShipmentSerializer(shipment).data)
