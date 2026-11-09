"""Payment domain rules: server-side amounts, verified confirmation, idempotent webhooks, refunds."""
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.notifications import audit
from apps.orders import services as order_services
from apps.orders.models import Order
from apps.projects.services import latest_valid_approval

from .models import Payment, Refund, WebhookEvent
from .providers import get_provider


MIN_AMOUNT_PAISE = 100  # Razorpay minimum


def _paise(amount: Decimal) -> int:
    return int((amount * 100).to_integral_value())


def start_payment(order: Order) -> tuple[Payment, object]:
    """Create (or reuse) a gateway order for the order's server-side total. Only after design approval."""
    provider = get_provider()
    order = Order.objects.select_for_update().get(pk=order.pk) if transaction.get_connection().in_atomic_block else order
    if order.payment_status != Order.PaymentStatus.UNPAID:
        raise ValidationError({"detail": "This order has already been paid."})
    if order.status != Order.Status.APPROVED:
        raise ValidationError({"detail": "Payment opens after you approve the final design."})
    project = order.items.get().project
    if not latest_valid_approval(project):
        raise ValidationError({"detail": "A valid design approval is required before payment."})
    if not order.shipping_address:
        raise ValidationError({"detail": "Add a delivery address before paying."})

    existing = order.payments.filter(status=Payment.Status.CREATED, amount=order.total_amount,
                                     provider=provider.name).first()
    if existing:
        return existing, provider
    if _paise(order.total_amount) < MIN_AMOUNT_PAISE:
        raise ValidationError({"detail": "The amount is below the minimum payable amount of 1 rupee."})
    gateway = provider.create_order(_paise(order.total_amount), order.currency, order.number)
    payment = Payment.objects.create(order=order, provider=provider.name, gateway_order_id=gateway["id"],
                                     amount=order.total_amount, currency=order.currency)
    return payment, provider


def _apply_to_order(order: Order) -> None:
    order.payment_status = Order.PaymentStatus.PAID
    order.save(update_fields=["payment_status", "updated_at"])
    if order.status == Order.Status.APPROVED:
        order_services.set_status(order, Order.Status.PAID, note="Payment verified")


@transaction.atomic
def mark_paid(payment_id, gateway_payment_id: str, amount_paise: int | None = None) -> Payment:
    """Idempotent. Safe to call from both the browser confirmation and the webhook."""
    payment = Payment.objects.select_for_update().get(pk=payment_id)
    if amount_paise is not None and amount_paise != _paise(payment.amount):
        raise ValidationError({"detail": "Paid amount does not match the order amount."})
    if payment.status == Payment.Status.PAID:
        return payment
    order = Order.objects.select_for_update().get(pk=payment.order_id)
    payment.status, payment.gateway_payment_id, payment.paid_at = Payment.Status.PAID, gateway_payment_id, timezone.now()
    if order.payment_status != Order.PaymentStatus.UNPAID:
        payment.is_duplicate = True  # customer was charged twice; needs a refund
    payment.save()
    audit.log("payment.paid", obj=payment, order=order.number, amount=payment.amount, duplicate=payment.is_duplicate)
    if not payment.is_duplicate:
        _apply_to_order(order)
    return payment


@transaction.atomic
def confirm_checkout(order: Order, gateway_order_id: str, payment_id: str, signature: str) -> Payment:
    payment = Payment.objects.filter(order=order, gateway_order_id=gateway_order_id).first()
    if not payment:
        raise ValidationError({"detail": "Unknown payment."})
    provider = get_provider()
    if not provider.verify_checkout_signature(gateway_order_id, payment_id, signature):
        raise ValidationError({"detail": "Payment verification failed."})
    return mark_paid(payment.pk, payment_id)


def mark_failed(payment: Payment, reason: str) -> None:
    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment.pk)
        if payment.status == Payment.Status.CREATED:  # never downgrade a paid payment
            payment.status, payment.failure_reason = Payment.Status.FAILED, reason[:200]
            payment.save(update_fields=["status", "failure_reason"])


def handle_webhook(body: bytes, signature: str, event_id: str, payload: dict) -> str:
    """Returns a short result string. Raises PermissionError for bad signatures."""
    provider = get_provider()
    if not provider.verify_webhook_signature(body, signature):
        raise PermissionError("bad signature")
    event_type = payload.get("event", "")
    try:
        # Dedupe record and processing share one transaction: a rejected event is rolled back, so the
        # provider's retry is processed again instead of being swallowed as a duplicate.
        with transaction.atomic():
            WebhookEvent.objects.create(provider=provider.name, event_id=event_id or "", event_type=event_type, payload=payload)
            return _process_event(event_type, payload)
    except IntegrityError:
        return "duplicate"


def _process_event(event_type: str, payload: dict) -> str:
    entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
    gw_order, gw_payment = entity.get("order_id"), entity.get("id", "")
    if event_type.startswith("payment.") or event_type == "order.paid":
        payment = Payment.objects.filter(gateway_order_id=gw_order).first()
        if not payment:
            return "unknown_order"
        if event_type in ("payment.captured", "order.paid"):
            mark_paid(payment.pk, gw_payment, entity.get("amount"))
            return "paid"
        if event_type == "payment.failed":
            mark_failed(payment, (entity.get("error_description") or "Payment failed"))
            return "failed"
    if event_type.startswith("refund."):
        ref = payload.get("payload", {}).get("refund", {}).get("entity", {})
        refund = Refund.objects.filter(gateway_refund_id=ref.get("id")).first()
        if refund:
            refund.status = Refund.Status.PROCESSED if event_type == "refund.processed" else Refund.Status.FAILED
            refund.save(update_fields=["status"])
            _refresh_refund_state(refund.payment)
            return "refund_updated"
    return "ignored"


def _refresh_refund_state(payment: Payment) -> None:
    order = payment.order
    refunded = sum((r.amount for r in payment.refunds.exclude(status=Refund.Status.FAILED)), Decimal("0"))
    if refunded <= 0 or payment.is_duplicate:
        return
    order.payment_status = (Order.PaymentStatus.REFUNDED if refunded >= payment.amount
                            else Order.PaymentStatus.PARTIALLY_REFUNDED)
    order.save(update_fields=["payment_status", "updated_at"])


@transaction.atomic
def refund_payment(payment: Payment, amount: Decimal, reason: str, actor) -> Refund:
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    if payment.status != Payment.Status.PAID:
        raise ValidationError({"detail": "Only paid payments can be refunded."})
    already = sum((r.amount for r in payment.refunds.exclude(status=Refund.Status.FAILED)), Decimal("0"))
    if amount <= 0 or already + amount > payment.amount:
        raise ValidationError({"amount": "Refund exceeds the refundable balance."})
    result = get_provider().refund(payment.gateway_payment_id, _paise(amount), reason)
    refund = Refund.objects.create(payment=payment, amount=amount, reason=reason, created_by=actor,
                                   gateway_refund_id=result["id"], status=result["status"])
    _refresh_refund_state(payment)
    audit.log("payment.refunded", actor=actor, obj=refund, payment=payment.pk, amount=amount, reason=reason)
    return refund
