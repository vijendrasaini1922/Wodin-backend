from django.db import models


class Invoice(models.Model):
    """Issued once per paid order. Amounts are frozen from the order's price snapshot."""

    order = models.OneToOneField("orders.Order", on_delete=models.PROTECT, related_name="invoice")
    number = models.CharField(max_length=20, unique=True)
    issued_at = models.DateTimeField(auto_now_add=True)
    pdf_key = models.CharField(max_length=300, blank=True)
    seller = models.JSONField(help_text="Seller details frozen at issue time")
    buyer = models.JSONField()
    lines = models.JSONField()
    totals = models.JSONField()

    class Meta:
        ordering = ["-issued_at"]
