import uuid

from django.conf import settings
from django.db import models


class AIDraftJob(models.Model):
    """One "Use AI" run. The draft is only a suggestion stored here; nothing touches the book until a designer saves it."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey("projects.BookProject", on_delete=models.CASCADE, related_name="ai_drafts")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED, db_index=True)
    provider = models.CharField(max_length=20, blank=True)
    model = models.CharField(max_length=60, blank=True)
    photos_sent = models.PositiveSmallIntegerField(default=0)
    tokens_in = models.PositiveIntegerField(default=0)
    tokens_out = models.PositiveIntegerField(default=0)
    pages = models.JSONField(default=list, blank=True, help_text="Editor-shaped pages: number, layout, photoIds, text")
    summary = models.JSONField(default=dict, blank=True, help_text="book_title, chapters, warnings")
    error = models.CharField(max_length=200, blank=True, help_text="Customer/staff-safe message; details are in the server log")
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
