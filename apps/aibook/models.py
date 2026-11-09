"""AI Book mode: a prompt plus photos in, a borderless photobook PDF (and an interactive viewer) out.

This is a standalone way to create a book. It has its own tables and never touches trips, projects, orders or payments.
"""
import uuid

from django.conf import settings
from django.db import models


class AIBook(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ASKING = "asking", "AI is reading the brief"
        QUESTIONS = "questions", "AI has questions"
        GENERATING = "generating", "Building the book"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    class Size(models.TextChoices):
        PORTRAIT = "portrait", "Portrait (A4)"
        SQUARE = "square", "Square"
        LANDSCAPE = "landscape", "Landscape (A4)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ai_books")
    project = models.ForeignKey("projects.BookProject", null=True, blank=True, on_delete=models.SET_NULL, related_name="ai_books")
    title = models.CharField(max_length=120, blank=True)
    prompt = models.TextField(blank=True)
    size = models.CharField(max_length=10, choices=Size.choices, default=Size.PORTRAIT)
    page_count = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Inner pages; empty = decided from the photos")
    allow_questions = models.BooleanField(default=True)
    premium = models.BooleanField(default=True, help_text="Art-directed album: mosaics, taped polaroids, pull-quotes and a route map")

    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT, db_index=True)
    stage = models.CharField(max_length=80, blank=True)
    progress = models.PositiveSmallIntegerField(default=0)
    error = models.CharField(max_length=200, blank=True)

    questions = models.JSONField(default=list, blank=True)   # [{id, question, hint}]
    answers = models.JSONField(default=dict, blank=True)     # {id: text}
    pages = models.JSONField(default=list, blank=True)       # planner output (element pages), kept for re-rendering
    summary = models.JSONField(default=dict, blank=True)

    pdf_key = models.CharField(max_length=300, blank=True)
    pdf_size = models.BigIntegerField(default=0)
    view_keys = models.JSONField(default=list, blank=True)   # one web-sized JPEG per page, cover first
    generations = models.PositiveSmallIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.title or f"AI book {self.id}"


class AIBookPhoto(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Awaiting upload"
        READY = "ready", "Ready"
        REJECTED = "rejected", "Rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    book = models.ForeignKey(AIBook, on_delete=models.CASCADE, related_name="photos")
    original_filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=40)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    reject_reason = models.CharField(max_length=200, blank=True)
    original_key = models.CharField(max_length=300)
    render_key = models.CharField(max_length=300, blank=True)   # sRGB JPEG, long side <= 2600 px: what pages are drawn from
    thumb_key = models.CharField(max_length=300, blank=True)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    taken_at = models.DateTimeField(null=True, blank=True)
    sha256 = models.CharField(max_length=64, blank=True)
    dhash = models.CharField(max_length=16, blank=True)
    flags = models.JSONField(default=list, blank=True)       # low_resolution, blurry, duplicate
    position = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["position", "created_at"]


class AIBookRun(models.Model):
    """One generation attempt: used for the daily limit and to see what each book costs."""

    book = models.ForeignKey(AIBook, on_delete=models.CASCADE, related_name="runs")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    model = models.CharField(max_length=60, blank=True)
    photos = models.PositiveIntegerField(default=0)
    tokens_in = models.PositiveIntegerField(default=0)
    tokens_out = models.PositiveIntegerField(default=0)
    ok = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
