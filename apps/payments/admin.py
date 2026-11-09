from django.contrib import admin

from .models import Payment, Refund, WebhookEvent

for m in (Payment, Refund, WebhookEvent):
    admin.site.register(m)
