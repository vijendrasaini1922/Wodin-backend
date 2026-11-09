import uuid

from django.conf import settings
from django.db import models


class SupportTicket(models.Model):
    class Category(models.TextChoices):
        QUESTION = "question", "Order question"
        UPLOAD = "upload", "Upload problem"
        PAYMENT = "payment", "Payment issue"
        DAMAGED = "damaged", "Damaged product"
        LOST = "lost", "Lost in transit"
        INCORRECT = "incorrect", "Incorrect product"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In progress"
        RESOLVED = "resolved", "Resolved"
        CLOSED = "closed", "Closed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    number = models.PositiveIntegerField(unique=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="tickets")
    order = models.ForeignKey("orders.Order", null=True, blank=True, on_delete=models.SET_NULL, related_name="tickets")
    contact_name = models.CharField(max_length=120, blank=True)
    contact_phone = models.CharField(max_length=16, blank=True)
    contact_email = models.EmailField(blank=True)
    category = models.CharField(max_length=12, choices=Category.choices, default=Category.QUESTION)
    subject = models.CharField(max_length=160)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN, db_index=True)
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="assigned_tickets")
    resolution = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = (SupportTicket.objects.aggregate(m=models.Max("number"))["m"] or 1000) + 1
        super().save(*args, **kwargs)


class TicketMessage(models.Model):
    ticket = models.ForeignKey(SupportTicket, on_delete=models.CASCADE, related_name="messages")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    from_staff = models.BooleanField(default=False)
    internal = models.BooleanField(default=False, help_text="Staff-only note, hidden from the customer")
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
