"""Order lifecycle rules. All status changes go through `set_status`."""
from django.core import signing
from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.catalog import pricing

from .models import Order, OrderItem, OrderStatusHistory

S = Order.Status

TRANSITIONS = {
    S.PLACED: {S.DESIGN, S.CANCELLED},
    S.DESIGN: {S.PREVIEW_READY, S.CANCELLED},
    S.PREVIEW_READY: {S.DESIGN, S.APPROVED, S.CANCELLED},
    S.APPROVED: {S.DESIGN, S.PREVIEW_READY, S.PAID, S.CANCELLED},
    S.PAID: {S.PRINTING, S.CANCELLED},
    S.PRINTING: {S.QUALITY_CHECK, S.CANCELLED},
    S.QUALITY_CHECK: {S.PRINTING, S.DISPATCHED},
    S.DISPATCHED: {S.DELIVERED},
    S.DELIVERED: set(),
    S.CANCELLED: set(),
}
PRE_PAYMENT = {S.PLACED, S.DESIGN, S.PREVIEW_READY, S.APPROVED}
TRACK_SALT = "wodin.order.track"


def tracking_token(order: Order) -> str:
    """Deterministic signed token; lets a customer (incl. guests) track an order via a link without logging in."""
    return signing.Signer(salt=TRACK_SALT).sign(str(order.id)).replace(":", "~")


def order_from_token(token: str) -> Order | None:
    try:
        order_id = signing.Signer(salt=TRACK_SALT).unsign(str(token).replace("~", ":"))
        return Order.objects.get(pk=order_id)
    except (signing.BadSignature, Order.DoesNotExist, ValueError, ValidationError):
        return None


@transaction.atomic
def set_status(order: Order, new_status: str, actor=None, note: str = "") -> Order:
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status == new_status:
        return order
    if new_status not in TRANSITIONS[order.status]:
        raise ValidationError({"detail": f"Cannot move order from '{order.status}' to '{new_status}'."})
    OrderStatusHistory.objects.create(order=order, from_status=order.status, to_status=new_status, actor=actor, note=note)
    previous = order.status
    order.status = new_status
    order.save(update_fields=["status", "updated_at"])

    if new_status == S.CANCELLED:
        from apps.projects.models import BookProject
        for item in order.items.select_related("project"):
            if item.project and item.project.status != BookProject.Status.CANCELLED:
                item.project.status = BookProject.Status.CANCELLED
                item.project.save(update_fields=["status", "updated_at"])

    from apps.notifications import audit, service as notifications

    audit.log("order.status_changed", actor=actor, obj=order, from_status=previous, to_status=new_status, note=note)
    transaction.on_commit(lambda: notifications.notify_order(order, new_status))
    return order


@transaction.atomic
def create_order_for_project(project, coupon_code: str = "") -> Order:
    if hasattr(project, "order_item"):
        return project.order_item.order
    user = project.user
    # An order is only accepted for a signed-in (Google or OTP-verified) account; guests sign in first (their data is then merged).
    if user.is_guest:
        raise ValidationError({"detail": "Please sign in before submitting.",
                               "code": "phone_verification_required"})
    snap = pricing.quote(variant_id=project.variant_id, page_count=project.page_count,
                         paper_id=project.paper_id, copies=project.copies,
                         addon_ids=project.addon_ids, coupon_code=coupon_code)
    order = Order.objects.create(
        user=user, contact_phone=user.display_phone, contact_name=user.full_name, contact_email=user.email,
        total_amount=snap["total"], price_snapshot=snap, coupon_code=snap["coupon"])
    OrderItem.objects.create(
        order=order, project=project, quantity=project.copies,
        title=f"{project.trip.title} - {snap['variant_name']}",
        configuration={"variant_id": project.variant_id, "variant": snap["variant_name"],
                       "page_count": project.page_count, "paper": snap["paper"],
                       "theme": project.theme.name if project.theme else None,
                       "addon_ids": project.addon_ids, "copies": project.copies})
    OrderStatusHistory.objects.create(order=order, to_status=S.PLACED, actor=user)
    return order


PROJECT_TO_ORDER = {
    "design": S.DESIGN,
    "changes_requested": S.DESIGN,
    "preview_ready": S.PREVIEW_READY,
    "approved": S.APPROVED,
}


def sync_from_project(project) -> None:
    """Keep the pre-payment order status aligned with the design workflow."""
    item = getattr(project, "order_item", None)
    if not item:
        return
    target = PROJECT_TO_ORDER.get(project.status)
    order = item.order
    if target and order.status in PRE_PAYMENT and order.status != target:
        # walk through DESIGN when the graph needs it (e.g. placed -> preview_ready)
        if target not in TRANSITIONS[order.status] and S.DESIGN in TRANSITIONS[order.status]:
            order = set_status(order, S.DESIGN)
        order = set_status(order, target, note="Design workflow update")
    if order.status == S.APPROVED and order.payment_status == Order.PaymentStatus.PAID:
        set_status(order, S.PAID, note="Payment verified")


def cancel(order: Order, actor) -> Order:
    if order.status not in PRE_PAYMENT:
        raise ValidationError({"detail": "This order can no longer be cancelled online. Please contact support."})
    return set_status(order, S.CANCELLED, actor=actor, note="Cancelled by customer")
