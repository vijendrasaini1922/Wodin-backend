from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.orders.models import Order
from apps.photos.storage import get_storage

from . import render
from .models import Invoice


def _next_number() -> str:
    year = timezone.now().year
    last = Invoice.objects.filter(number__startswith=f"WD/{year}/").order_by("-number").first()
    seq = int(last.number.rsplit("/", 1)[1]) + 1 if last else 1
    return f"WD/{year}/{seq:05d}"


def get_or_issue(order: Order) -> Invoice:
    if order.payment_status not in (Order.PaymentStatus.PAID, Order.PaymentStatus.PARTIALLY_REFUNDED, Order.PaymentStatus.REFUNDED):
        raise ValidationError({"detail": "An invoice is available once the order has been paid."})
    existing = Invoice.objects.filter(order=order).first()
    if existing:
        return existing
    snap, addr = order.price_snapshot, order.shipping_address or {}
    for _ in range(3):  # retry on a concurrent sequence collision
        try:
            with transaction.atomic():
                invoice = Invoice.objects.create(
                    order=order, number=_next_number(),
                    seller={"name": getattr(settings, "SELLER_NAME", "WoDin"), "address": getattr(settings, "SELLER_ADDRESS", ""),
                            "gstin": getattr(settings, "SELLER_GSTIN", "")},
                    buyer={"order_number": order.number, "name": order.contact_name or addr.get("recipient_name", ""),
                           "phone": order.contact_phone, "email": order.contact_email,
                           "address_lines": [x for x in [addr.get("line1"), addr.get("line2"),
                                                         f"{addr.get('city', '')}, {addr.get('state', '')} {addr.get('pincode', '')}".strip(" ,")] if x]},
                    lines=snap["lines"],
                    totals={k: snap[k] for k in ("subtotal", "discount", "shipping", "tax", "prices_include_tax", "total")})
                invoice.pdf_key = f"invoices/{invoice.number.replace('/', '-')}.pdf"
                get_storage().save(invoice.pdf_key, render.render(invoice), "application/pdf")
                invoice.save(update_fields=["pdf_key"])
                return invoice
        except IntegrityError:
            continue
    raise ValidationError({"detail": "Could not issue invoice. Please try again."})
