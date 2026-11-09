from rest_framework import serializers

from .models import Shipment, ShipmentEvent


class EventSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShipmentEvent
        fields = ["status", "location", "note", "occurred_at"]


class ShipmentSerializer(serializers.ModelSerializer):
    events = EventSerializer(many=True, read_only=True)
    order_number = serializers.CharField(source="order.number", read_only=True)

    class Meta:
        model = Shipment
        fields = ["id", "order", "order_number", "courier", "tracking_number", "tracking_url", "dispatched_at",
                  "delivered_at", "events"]
        read_only_fields = fields


class DispatchSerializer(serializers.Serializer):
    order_id = serializers.UUIDField()
    courier = serializers.CharField(max_length=60)
    tracking_number = serializers.CharField(max_length=80)
    tracking_url = serializers.URLField(required=False, allow_blank=True, default="")


class EventCreateSerializer(serializers.ModelSerializer):
    occurred_at = serializers.DateTimeField(required=False)

    class Meta:
        model = ShipmentEvent
        fields = ["status", "location", "note", "occurred_at"]
