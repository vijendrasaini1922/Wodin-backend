import uuid

from django.conf import settings
from django.db import models


class BookProject(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        DESIGN = "design", "Design in progress"
        PREVIEW_READY = "preview_ready", "Preview ready"
        CHANGES_REQUESTED = "changes_requested", "Changes requested"
        APPROVED = "approved", "Approved"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="projects")
    trip = models.ForeignKey("trips.Trip", on_delete=models.CASCADE, related_name="projects")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT)
    paper = models.ForeignKey("catalog.Paper", null=True, blank=True, on_delete=models.PROTECT)
    theme = models.ForeignKey("catalog.Theme", null=True, blank=True, on_delete=models.PROTECT)
    page_count = models.PositiveSmallIntegerField()
    copies = models.PositiveSmallIntegerField(default=1)
    addon_ids = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True)
    designer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="assigned_projects")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]


class BookPage(models.Model):
    """Structured working layout edited by designers. Revisions snapshot this."""

    project = models.ForeignKey(BookProject, on_delete=models.CASCADE, related_name="pages")
    number = models.PositiveSmallIntegerField(help_text="0 = cover, 1..N = inner pages")
    layout = models.CharField(max_length=40, default="single")

    class Meta:
        ordering = ["number"]
        unique_together = [("project", "number")]


class PageElement(models.Model):
    class Kind(models.TextChoices):
        PHOTO = "photo", "Photo"
        TEXT = "text", "Text"

    page = models.ForeignKey(BookPage, on_delete=models.CASCADE, related_name="elements")
    kind = models.CharField(max_length=8, choices=Kind.choices)
    photo = models.ForeignKey("photos.Photo", null=True, blank=True, on_delete=models.SET_NULL)
    text = models.TextField(blank=True)
    # Geometry as fractions (0..1) of the page trim box so previews render without layout shift.
    x = models.FloatField()
    y = models.FloatField()
    w = models.FloatField()
    h = models.FloatField()
    z = models.PositiveSmallIntegerField(default=0)
    style = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["z", "id"]


class BookRevision(models.Model):
    class Status(models.TextChoices):
        SENT = "sent", "Sent to customer"
        SUPERSEDED = "superseded", "Superseded"
        APPROVED = "approved", "Approved"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(BookProject, on_delete=models.CASCADE, related_name="revisions")
    number = models.PositiveIntegerField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.SENT)
    snapshot = models.JSONField(help_text="Immutable structured pages+elements at creation time")
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-number"]
        unique_together = [("project", "number")]


class Approval(models.Model):
    """Customer approval of one exact revision. Invalidated (never deleted) if the design later changes."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    revision = models.OneToOneField(BookRevision, on_delete=models.PROTECT, related_name="approval")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    approved_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    invalidated_at = models.DateTimeField(null=True, blank=True)
    invalidated_reason = models.CharField(max_length=200, blank=True)

    @property
    def is_valid(self) -> bool:
        return self.invalidated_at is None


class ChangeRequest(models.Model):
    revision = models.ForeignKey(BookRevision, on_delete=models.CASCADE, related_name="change_requests")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    page_number = models.PositiveSmallIntegerField(null=True, blank=True)
    note = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    resolved = models.BooleanField(default=False)

    class Meta:
        ordering = ["created_at"]
