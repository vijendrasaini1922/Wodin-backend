import secrets
import uuid

from django.conf import settings
from django.db import models

NUMBER_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I


def generate_order_number() -> str:
    return "WD-" + "".join(secrets.choice(NUMBER_ALPHABET) for _ in range(8))


class Order(models.Model):
    class Status(models.TextChoices):
        PLACED = "placed", "Order placed"
        DESIGN = "design", "Design in progress"
        PREVIEW_READY = "preview_ready", "Preview ready - approval pending"
        APPROVED = "approved", "Approved"
        PAID = "paid", "Payment verified"
        PRINTING = "printing", "Printing and binding"
        QUALITY_CHECK = "quality_check", "Quality check"
        DISPATCHED = "dispatched", "Dispatched"
        DELIVERED = "delivered", "Delivered"
        CANCELLED = "cancelled", "Cancelled"

    class PaymentStatus(models.TextChoices):
        UNPAID = "unpaid", "Unpaid"
        PAID = "paid", "Paid"
        PARTIALLY_REFUNDED = "partially_refunded", "Partially refunded"
        REFUNDED = "refunded", "Refunded"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    number = models.CharField(max_length=16, unique=True, default=generate_order_number)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                             related_name="orders")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PLACED, db_index=True)
    payment_status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.UNPAID,
                                      db_index=True)

    contact_phone = models.CharField(max_length=16)
    contact_name = models.CharField(max_length=120, blank=True)
    contact_email = models.EmailField(blank=True)
    shipping_address = models.JSONField(null=True, blank=True, help_text="Snapshot of the delivery address")

    currency = models.CharField(max_length=3, default="INR")
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    price_snapshot = models.JSONField(help_text="Full pricing breakdown frozen at order time")
    coupon_code = models.CharField(max_length=40, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.number


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    project = models.OneToOneField("projects.BookProject", on_delete=models.PROTECT, related_name="order_item")
    title = models.CharField(max_length=200)
    quantity = models.PositiveSmallIntegerField(default=1)
    configuration = models.JSONField(help_text="Frozen product configuration (variant, pages, paper, add-ons)")


class OrderStatusHistory(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="history")
    from_status = models.CharField(max_length=16, blank=True)
    to_status = models.CharField(max_length=16)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="+")
    note = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
