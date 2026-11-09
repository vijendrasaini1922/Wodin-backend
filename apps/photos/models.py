import uuid

from django.conf import settings
from django.db import models


class PhotoGroup(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    trip = models.ForeignKey("trips.Trip", on_delete=models.CASCADE, related_name="photo_groups")
    title = models.CharField(max_length=80)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "title"]


class Photo(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Awaiting upload"
        UPLOADED = "uploaded", "Uploaded, processing"
        READY = "ready", "Ready"
        REJECTED = "rejected", "Rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="photos")
    trip = models.ForeignKey("trips.Trip", on_delete=models.CASCADE, related_name="photos")
    group = models.ForeignKey(PhotoGroup, null=True, blank=True, on_delete=models.SET_NULL, related_name="photos")

    original_filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=40)
    size_bytes = models.BigIntegerField(default=0)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    reject_reason = models.CharField(max_length=200, blank=True)

    # Private storage keys. The original is never modified; edits live in derivatives.
    original_key = models.CharField(max_length=300)
    thumb_key = models.CharField(max_length=300, blank=True)
    preview_key = models.CharField(max_length=300, blank=True)

    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    taken_at = models.DateTimeField(null=True, blank=True)
    sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    dhash = models.CharField(max_length=16, blank=True)
    blur_score = models.FloatField(null=True, blank=True)
    flags = models.JSONField(default=list, blank=True, help_text="low_resolution, blurry, duplicate, near_duplicate")
    duplicate_of = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="duplicates")

    # Customer edits (non-destructive)
    rotation = models.PositiveSmallIntegerField(default=0)  # 0/90/180/270 clockwise
    crop = models.JSONField(null=True, blank=True, help_text="fractions {x,y,w,h} of the rotated image")
    is_favourite = models.BooleanField(default=False)
    caption = models.CharField(max_length=200, blank=True)
    position = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["position", "taken_at", "created_at"]
        indexes = [models.Index(fields=["trip", "position"])]
