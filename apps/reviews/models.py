import uuid

from django.conf import settings
from django.db import models


class Review(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending moderation"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        HIDDEN = "hidden", "Hidden"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reviews")
    # Verified purchase is derived from a delivered order, never from user input.
    order = models.OneToOneField("orders.Order", null=True, blank=True, on_delete=models.SET_NULL, related_name="review")
    is_verified = models.BooleanField(default=False)
    is_demo = models.BooleanField(default=False, help_text="Seed/sample data; never shown as a real customer review")

    rating = models.PositiveSmallIntegerField()
    title = models.CharField(max_length=120)
    body = models.TextField()
    display_name = models.CharField(max_length=80)
    destination = models.CharField(max_length=120, blank=True)

    consent_publish_review = models.BooleanField(default=False)
    consent_publish_images = models.BooleanField(default=False)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    is_featured = models.BooleanField(default=False)
    response = models.TextField(blank=True)
    removal_requested = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_featured", "-created_at"]


class ReviewImage(models.Model):
    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="images")
    storage_key = models.CharField(max_length=300)
    approved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
