from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.orders import services as order_services
from apps.orders.models import Order
from apps.production.models import ProductionJob

from .models import Shipment, ShipmentEvent


@transaction.atomic
def dispatch(order: Order, courier: str, tracking_number: str, tracking_url: str, actor) -> Shipment:
    order = Order.objects.select_for_update().get(pk=order.pk)
    job = getattr(order, "production", None)
    if not job or job.status != ProductionJob.Status.PASSED:
        raise ValidationError({"detail": "The book must pass quality check before it can be dispatched."})
    if not order.shipping_address:
        raise ValidationError({"detail": "The order has no delivery address."})
    if hasattr(order, "shipment"):
        raise ValidationError({"detail": "This order has already been dispatched."})
    shipment = Shipment.objects.create(order=order, courier=courier, tracking_number=tracking_number,
                                       tracking_url=tracking_url, created_by=actor)
    ShipmentEvent.objects.create(shipment=shipment, status="Dispatched", occurred_at=timezone.now())
    order_services.set_status(order, Order.Status.DISPATCHED, actor=actor, note=f"{courier} {tracking_number}"[:300])
    return shipment


@transaction.atomic
def add_event(shipment: Shipment, status: str, location: str, note: str, occurred_at=None) -> ShipmentEvent:
    return ShipmentEvent.objects.create(shipment=shipment, status=status, location=location, note=note,
                                        occurred_at=occurred_at or timezone.now())


@transaction.atomic
def mark_delivered(shipment: Shipment, actor) -> Shipment:
    shipment = Shipment.objects.select_for_update().get(pk=shipment.pk)
    if shipment.delivered_at:
        return shipment
    shipment.delivered_at = timezone.now()
    shipment.save(update_fields=["delivered_at"])
    ShipmentEvent.objects.create(shipment=shipment, status="Delivered", occurred_at=shipment.delivered_at)
    order_services.set_status(shipment.order, Order.Status.DELIVERED, actor=actor, note="Delivered")
    return shipment
