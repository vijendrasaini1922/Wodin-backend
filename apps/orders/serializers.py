from rest_framework import serializers

from apps.accounts.serializers import AddressSerializer

from .models import Order, OrderItem, OrderStatusHistory
from .services import tracking_token

# Customer-facing milestone list (plan section 12) with the statuses that complete each step.
TIMELINE = [
    ("placed", "Order placed"), ("design", "Design in progress"), ("preview_ready", "Preview ready"),
    ("approved", "Approved"), ("paid", "Payment verified"), ("printing", "Printing and binding"),
    ("quality_check", "Quality check"), ("dispatched", "Dispatched"), ("delivered", "Delivered"),
]
ORDER_INDEX = {k: i for i, (k, _) in enumerate(TIMELINE)}


def build_timeline(order: Order) -> list[dict]:
    reached = {h.to_status for h in order.history.all()}
    current = order.status
    steps = []
    for key, label in TIMELINE:
        done = key in reached or (current in ORDER_INDEX and ORDER_INDEX[key] < ORDER_INDEX[current])
        steps.append({"key": key, "label": label, "done": done, "current": key == current})
    return steps


class OrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = ["id", "project", "title", "quantity", "configuration"]


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    timeline = serializers.SerializerMethodField()
    tracking_token = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = ["id", "number", "status", "payment_status", "contact_phone", "contact_name", "contact_email",
                  "shipping_address", "currency", "total_amount", "price_snapshot", "coupon_code", "items",
                  "timeline", "tracking_token", "created_at"]
        read_only_fields = fields

    def get_timeline(self, obj):
        return build_timeline(obj)

    def get_tracking_token(self, obj):
        return tracking_token(obj)


class OrderAddressSerializer(serializers.Serializer):
    address_id = serializers.UUIDField(required=False)
    contact_email = serializers.EmailField(required=False, allow_blank=True)
    address = AddressSerializer(required=False)

    def validate(self, attrs):
        if not attrs.get("address_id") and not attrs.get("address"):
            raise serializers.ValidationError({"address": "Provide address_id or an address."})
        return attrs
