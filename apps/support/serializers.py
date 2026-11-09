from rest_framework import serializers

from .models import SupportTicket, TicketMessage


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = TicketMessage
        fields = ["id", "from_staff", "body", "created_at"]


class StaffMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = TicketMessage
        fields = ["id", "from_staff", "internal", "body", "created_at"]


class TicketSerializer(serializers.ModelSerializer):
    messages = serializers.SerializerMethodField()
    order_number = serializers.CharField(source="order.number", read_only=True, default=None)
    message = serializers.CharField(write_only=True, min_length=5)

    class Meta:
        model = SupportTicket
        fields = ["id", "number", "order", "order_number", "category", "subject", "status", "resolution",
                  "contact_name", "contact_phone", "contact_email", "created_at", "updated_at", "messages", "message"]
        read_only_fields = ["id", "number", "status", "resolution", "created_at", "updated_at", "order_number"]

    def get_messages(self, obj):
        return MessageSerializer(obj.messages.filter(internal=False), many=True).data

    def validate_order(self, order):
        user = self.context["request"].user
        if order and order.user_id != user.id:
            raise serializers.ValidationError("Choose one of your orders.")
        return order


class StaffTicketSerializer(serializers.ModelSerializer):
    messages = StaffMessageSerializer(many=True, read_only=True)
    order_number = serializers.CharField(source="order.number", read_only=True, default=None)

    class Meta:
        model = SupportTicket
        fields = ["id", "number", "order", "order_number", "user", "category", "subject", "status", "assignee",
                  "resolution", "contact_name", "contact_phone", "contact_email", "created_at", "updated_at", "messages"]
        read_only_fields = ["id", "number", "order", "order_number", "user", "category", "subject", "contact_name",
                            "contact_phone", "contact_email", "created_at", "updated_at", "messages"]
