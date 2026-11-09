from django.db.models import Avg, Count, Q
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.core.permissions import IsSupport
from apps.orders.models import Order

from .models import Review
from .serializers import AdminReviewSerializer, MyReviewSerializer, PublicReviewSerializer


def public_queryset():
    """Only moderated, consented, non-demo reviews are ever public."""
    return Review.objects.filter(status=Review.Status.APPROVED, consent_publish_review=True,
                                 removal_requested=False, is_demo=False)


class PublicReviewViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [AllowAny]
    authentication_classes: list = []
    serializer_class = PublicReviewSerializer

    def get_queryset(self):
        qs = public_queryset()
        p = self.request.query_params
        if p.get("destination"):
            qs = qs.filter(destination__iexact=p["destination"])
        if p.get("rating"):
            qs = qs.filter(rating=p["rating"])
        if p.get("featured") == "1":
            qs = qs.filter(is_featured=True)
        return qs

    @action(detail=False, methods=["get"])
    def summary(self, request):
        agg = public_queryset().aggregate(count=Count("id"), average=Avg("rating"),
                                          verified=Count("id", filter=Q(is_verified=True)))
        return Response({"count": agg["count"], "average": round(agg["average"], 2) if agg["average"] else None,
                         "verified_count": agg["verified"]})


class MyReviewViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                      mixins.UpdateModelMixin, viewsets.GenericViewSet):
    serializer_class = MyReviewSerializer
    pagination_class = None
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        return Review.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        order = serializer.validated_data.get("order")
        if order is None or order.user_id != self.request.user.id:
            raise ValidationError({"order": "Choose one of your orders."})
        if order.status != Order.Status.DELIVERED:
            raise ValidationError({"order": "You can review an order after it has been delivered."})
        if Review.objects.filter(order=order).exists():
            raise ValidationError({"order": "You have already reviewed this order."})
        serializer.save(user=self.request.user, is_verified=True)  # derived from the delivered order

    def perform_update(self, serializer):
        # Editing resets moderation so changed text is re-checked; rating/consent edits are allowed.
        serializer.save(status=Review.Status.PENDING)

    @action(detail=True, methods=["post"], url_path="request-removal")
    def request_removal(self, request, pk=None):
        review = self.get_object()
        review.removal_requested = True
        review.save(update_fields=["removal_requested", "updated_at"])
        return Response(MyReviewSerializer(review).data)


class AdminReviewViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin,
                         viewsets.GenericViewSet):
    permission_classes = [IsSupport]
    serializer_class = AdminReviewSerializer
    http_method_names = ["get", "patch", "head", "options"]

    def get_queryset(self):
        qs = Review.objects.all()
        p = self.request.query_params
        for field in ("status", "rating"):
            if p.get(field):
                qs = qs.filter(**{field: p[field]})
        if p.get("destination"):
            qs = qs.filter(destination__iexact=p["destination"])
        return qs

    def perform_update(self, serializer):
        review = serializer.instance
        new_status = serializer.validated_data.get("status", review.status)
        if new_status != Review.Status.APPROVED and "is_featured" not in serializer.validated_data:
            serializer.validated_data["is_featured"] = False  # hiding/rejecting also un-features
        featured = serializer.validated_data.get("is_featured", review.is_featured)
        if (new_status == Review.Status.APPROVED or featured) and not review.consent_publish_review:
            raise ValidationError({"detail": "The customer has not given permission to publish this review."})
        if featured and new_status != Review.Status.APPROVED:
            raise ValidationError({"is_featured": "Only approved reviews can be featured."})
        serializer.save()
