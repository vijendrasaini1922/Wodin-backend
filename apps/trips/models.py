import uuid

from django.conf import settings
from django.db import models


class Trip(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="trips")
    title = models.CharField(max_length=120, help_text="e.g. Manali Memories")
    destinations = models.JSONField(default=list, help_text="List of destination names")
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    companions = models.JSONField(default=list, blank=True)
    description = models.TextField(blank=True)
    special_message = models.TextField(blank=True)
    cover_title = models.CharField(max_length=120, blank=True)
    dedication = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.title
