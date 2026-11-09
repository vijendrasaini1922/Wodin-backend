from rest_framework import serializers

from .models import Payment, Refund


class RefundSerializer(serializers.ModelSerializer):
    class Meta:
        model = Refund
        fields = ["id", "amount", "status", "reason", "created_at"]


class PaymentSerializer(serializers.ModelSerializer):
    refunds = RefundSerializer(many=True, read_only=True)

    class Meta:
        model = Payment
        fields = ["id", "gateway_order_id", "gateway_payment_id", "amount", "currency", "status", "created_at",
                  "paid_at", "refunds"]


class ConfirmSerializer(serializers.Serializer):
    gateway_order_id = serializers.CharField()
    gateway_payment_id = serializers.CharField()
    signature = serializers.CharField()


class RefundRequestSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    reason = serializers.CharField(max_length=200)
