"""Dashboard and analytics aggregates for back-office users."""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Avg, Count, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from apps.orders.models import Order
from apps.payments.models import Payment, Refund
from apps.photos.models import Photo
from apps.production.models import ProductionJob
from apps.projects.models import BookProject
from apps.reviews.models import Review
from apps.support.models import SupportTicket

S = Order.Status
ACTIVE = [S.PLACED, S.DESIGN, S.PREVIEW_READY, S.APPROVED, S.PAID, S.PRINTING, S.QUALITY_CHECK, S.DISPATCHED]


def _money(value) -> str:
    return f"{(value or Decimal('0')):.2f}"


def dashboard() -> dict:
    counts = dict(Order.objects.values_list("status").annotate(n=Count("id")))
    paid = Payment.objects.filter(status=Payment.Status.PAID, is_duplicate=False)
    refunded = Refund.objects.exclude(status=Refund.Status.FAILED).aggregate(s=Sum("amount"))["s"]
    revenue = paid.aggregate(s=Sum("amount"))["s"]
    return {
        "orders": {
            "total": sum(counts.values()),
            "new_today": Order.objects.filter(created_at__date=timezone.localdate()).count(),
            "active": sum(counts.get(s, 0) for s in ACTIVE),
            "by_status": {s.value: counts.get(s, 0) for s in S},
        },
        "pipeline": {
            "pending_uploads": BookProject.objects.filter(status=BookProject.Status.DRAFT).count(),
            "awaiting_design": BookProject.objects.filter(status__in=[BookProject.Status.DESIGN, BookProject.Status.CHANGES_REQUESTED]).count(),
            "awaiting_customer_approval": BookProject.objects.filter(status=BookProject.Status.PREVIEW_READY).count(),
            "approved_unpaid": Order.objects.filter(status=S.APPROVED).count(),
            "failed_payments": Payment.objects.filter(status=Payment.Status.FAILED).count(),
            "duplicate_payments_to_refund": Payment.objects.filter(is_duplicate=True).count(),
            "awaiting_printing": ProductionJob.objects.filter(status=ProductionJob.Status.READY).count()
                                 + Order.objects.filter(status=S.PAID).exclude(production__isnull=False).count(),
            "in_printing": ProductionJob.objects.filter(status=ProductionJob.Status.PRINTING).count(),
            "awaiting_quality_check": ProductionJob.objects.filter(status=ProductionJob.Status.QUALITY_CHECK).count(),
            "ready_to_dispatch": ProductionJob.objects.filter(status=ProductionJob.Status.PASSED, order__shipment__isnull=True).count(),
            "dispatched": counts.get(S.DISPATCHED, 0),
            "delivered": counts.get(S.DELIVERED, 0),
        },
        "money": {"revenue": _money(revenue), "refunds": _money(refunded), "net": _money((revenue or Decimal("0")) - (refunded or Decimal("0")))},
        "support": {"open_tickets": SupportTicket.objects.filter(status__in=["open", "in_progress"]).count(),
                    "complaints": SupportTicket.objects.filter(category__in=["damaged", "lost", "incorrect"], status__in=["open", "in_progress"]).count()},
        "reviews_pending": Review.objects.filter(status=Review.Status.PENDING).count(),
    }


def analytics(days: int = 30) -> dict:
    since = timezone.now() - timedelta(days=days)
    orders = Order.objects.filter(created_at__gte=since).exclude(status=S.CANCELLED)
    paid_orders = Order.objects.filter(payment_status__in=["paid", "partially_refunded", "refunded"], created_at__gte=since)
    total = orders.count()
    paid_count = paid_orders.count()
    revenue = paid_orders.aggregate(s=Sum("total_amount"))["s"]
    aov = paid_orders.aggregate(a=Avg("total_amount"))["a"]
    customers_with_orders = (Order.objects.filter(user__is_guest=False, payment_status__in=["paid", "partially_refunded"])
                             .values("user").annotate(n=Count("id")))
    repeat = sum(1 for c in customers_with_orders if c["n"] > 1)
    uploaded = BookProject.objects.filter(created_at__gte=since).count()
    daily = (paid_orders.annotate(d=TruncDate("created_at")).values("d").annotate(orders=Count("id"), revenue=Sum("total_amount")).order_by("d"))
    return {
        "days": days,
        "orders_created": total,
        "orders_paid": paid_count,
        "projects_started": uploaded,
        "project_to_paid_conversion_pct": round(100 * paid_count / uploaded, 1) if uploaded else None,
        "revenue": _money(revenue),
        "average_order_value": _money(aov),
        "repeat_customers": repeat,
        "repeat_customer_rate_pct": round(100 * repeat / len(customers_with_orders), 1) if customers_with_orders else None,
        "daily": [{"date": str(r["d"]), "orders": r["orders"], "revenue": _money(r["revenue"])} for r in daily],
        "ratings": {"average": Review.objects.filter(status="approved", is_demo=False).aggregate(a=Avg("rating"))["a"],
                    "count": Review.objects.filter(status="approved", is_demo=False).count()},
        "photo_flags": {"photos": Photo.objects.filter(status="ready").count(),
                        "flagged": Photo.objects.filter(status="ready").exclude(flags=[]).count()},
    }
