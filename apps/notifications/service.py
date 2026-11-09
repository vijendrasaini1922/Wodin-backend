"""Transactional notifications. Delivery is best-effort and never blocks the business transaction."""
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from .models import Notification

log = logging.getLogger(__name__)

# Customer-friendly copy for order milestones (plan section 11: notify on meaningful status changes).
ORDER_MESSAGES = {
    "design": ("Design in progress", "We've started designing your WoDin photobook {number}."),
    "preview_ready": ("Your preview is ready", "The preview for order {number} is ready. Please review and approve it in My WoDin."),
    "approved": ("Design approved", "Thanks for approving order {number}. Complete payment to start printing."),
    "paid": ("Payment received", "We've received your payment for order {number}. Printing is next."),
    "printing": ("Printing started", "Your photobook {number} is being printed and bound."),
    "dispatched": ("Your book is on its way", "Order {number} has been dispatched. Track it in My WoDin."),
    "delivered": ("Delivered", "Order {number} has been delivered. We'd love your review!"),
    "cancelled": ("Order cancelled", "Order {number} has been cancelled."),
}


def _deliver(n: Notification) -> None:
    try:
        if n.channel == Notification.Channel.EMAIL:
            send_mail(n.subject, n.body, settings.DEFAULT_FROM_EMAIL, [n.recipient], fail_silently=False)
        else:  # SMS: log in dev; wire a DLT-approved transactional SMS provider for production
            log.info("SMS to %s: %s", n.recipient, n.body)
        n.status, n.sent_at = Notification.Status.SENT, timezone.now()
    except Exception as exc:  # noqa: BLE001 - delivery problems must not break orders
        n.status, n.error = Notification.Status.FAILED, str(exc)[:200]
    n.save(update_fields=["status", "error", "sent_at"])


def notify_order(order, kind: str) -> list[Notification]:
    template = ORDER_MESSAGES.get(kind)
    if not template:
        return []
    subject, body = template[0], template[1].format(number=order.number)
    out = []
    user = order.user
    consent = True if user is None or not hasattr(user, "profile") else user.profile.transactional_consent
    channels = [(Notification.Channel.SMS, order.contact_phone)]
    if order.contact_email:
        channels.append((Notification.Channel.EMAIL, order.contact_email))
    for channel, recipient in channels:
        n = Notification.objects.create(user=user, order=order, channel=channel, kind=f"order.{kind}", recipient=recipient,
                                        subject=subject, body=body,
                                        status=Notification.Status.QUEUED if consent and not recipient.startswith("guest:") else Notification.Status.SKIPPED)
        if n.status == Notification.Status.QUEUED:
            _deliver(n)
        out.append(n)
    return out
