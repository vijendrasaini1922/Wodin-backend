from rest_framework import serializers

from apps.accounts.models import Role, User
from apps.catalog.models import AddOn, Coupon, Paper, PricingSettings, Product, ProductVariant, Theme
from apps.orders.models import Order
from apps.orders.serializers import OrderItemSerializer, build_timeline


class AdminOrderListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Order
        fields = ["id", "number", "status", "payment_status", "contact_name", "contact_phone", "total_amount", "created_at"]


class AdminOrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    timeline = serializers.SerializerMethodField()
    history = serializers.SerializerMethodField()
    payments = serializers.SerializerMethodField()
    approval = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = ["id", "number", "status", "payment_status", "contact_name", "contact_phone", "contact_email",
                  "shipping_address", "total_amount", "price_snapshot", "coupon_code", "items", "timeline",
                  "history", "payments", "approval", "created_at"]

    def get_timeline(self, obj):
        return build_timeline(obj)

    def get_history(self, obj):
        return [{"from": h.from_status, "to": h.to_status, "note": h.note, "actor": str(h.actor) if h.actor else None,
                 "at": h.created_at} for h in obj.history.all()]

    def get_payments(self, obj):
        return [{"id": str(p.id), "status": p.status, "amount": str(p.amount), "gateway_payment_id": p.gateway_payment_id,
                 "is_duplicate": p.is_duplicate, "refunded": str(sum((r.amount for r in p.refunds.all() if r.status != "failed"), 0))}
                for p in obj.payments.all()]

    def get_approval(self, obj):
        from apps.projects.services import latest_valid_approval
        item = obj.items.first()
        a = latest_valid_approval(item.project) if item else None
        return {"revision_number": a.revision.number, "approved_at": a.approved_at, "by": a.user.phone} if a else None


class CustomerSerializer(serializers.ModelSerializer):
    orders_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = User
        fields = ["id", "phone", "full_name", "email", "role", "is_guest", "is_active", "created_at", "orders_count"]
        read_only_fields = fields


class StaffUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "phone", "full_name", "email", "role", "is_active"]
        read_only_fields = ["id", "phone"]


# ---- catalogue management (all prices/options are admin-configurable) ----
class AdminVariantSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductVariant
        fields = "__all__"


class AdminProductSerializer(serializers.ModelSerializer):
    variants = AdminVariantSerializer(many=True, read_only=True)

    class Meta:
        model = Product
        fields = ["id", "name", "slug", "tagline", "description", "is_active", "sort_order", "variants"]


class AdminPaperSerializer(serializers.ModelSerializer):
    class Meta:
        model = Paper
        fields = "__all__"


class AdminThemeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Theme
        fields = "__all__"


class AdminAddOnSerializer(serializers.ModelSerializer):
    class Meta:
        model = AddOn
        fields = "__all__"


class AdminCouponSerializer(serializers.ModelSerializer):
    class Meta:
        model = Coupon
        fields = "__all__"
        read_only_fields = ["used_count"]

    def validate(self, attrs):
        kind, value = attrs.get("kind", getattr(self.instance, "kind", None)), attrs.get("value", getattr(self.instance, "value", 0))
        if kind == Coupon.Kind.PERCENT and not 0 < value <= 100:
            raise serializers.ValidationError({"value": "Percent must be between 0 and 100."})
        if value <= 0:
            raise serializers.ValidationError({"value": "Must be greater than zero."})
        return attrs


class PricingSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = PricingSettings
        exclude = ["id"]
