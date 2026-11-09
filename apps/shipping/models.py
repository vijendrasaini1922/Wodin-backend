from django.conf import settings
from django.db import models


class Shipment(models.Model):
    order = models.OneToOneField("orders.Order", on_delete=models.PROTECT, related_name="shipment")
    courier = models.CharField(max_length=60)
    tracking_number = models.CharField(max_length=80)
    tracking_url = models.URLField(blank=True)
    dispatched_at = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")


class ShipmentEvent(models.Model):
    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="events")
    status = models.CharField(max_length=60)
    location = models.CharField(max_length=120, blank=True)
    note = models.CharField(max_length=200, blank=True)
    occurred_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["occurred_at", "id"]


class NonServiceablePincode(models.Model):
    """Used by the manual courier. Replace with a live aggregator check via ShippingProvider."""

    prefix = models.CharField(max_length=6, unique=True, help_text="PIN code or leading digits that cannot be served")
    reason = models.CharField(max_length=120, blank=True)
