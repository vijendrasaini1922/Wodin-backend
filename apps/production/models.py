from django.conf import settings
from django.db import models

QC_ITEMS = [
    ("size_pages_order", "Correct book size, page count and page order"),
    ("images_ok", "No missing, corrupted or low-resolution photographs"),
    ("cover_binding", "Correct cover, spine and binding specification"),
    ("no_defects", "No visible printing, trimming or binding defects"),
    ("details_checked", "Customer name, shipping address and order details checked"),
    ("final_qc", "Final quality check recorded before dispatch"),
]


def default_checklist():
    return {k: False for k, _ in QC_ITEMS}


class ProductionJob(models.Model):
    class Status(models.TextChoices):
        READY = "ready", "Ready for production"
        PRINTING = "printing", "Printing and binding"
        QUALITY_CHECK = "quality_check", "Quality check"
        PASSED = "passed", "QC passed"

    order = models.OneToOneField("orders.Order", on_delete=models.PROTECT, related_name="production")
    status = models.CharField(max_length=14, choices=Status.choices, default=Status.READY, db_index=True)
    revision = models.ForeignKey("projects.BookRevision", null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="+", help_text="The approved revision this print file was built from")
    print_pdf_key = models.CharField(max_length=300, blank=True)
    pdf_report = models.JSONField(default=dict, blank=True)
    pdf_valid = models.BooleanField(default=False)
    checklist = models.JSONField(default=default_checklist)
    qc_notes = models.TextField(blank=True)
    qc_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    printing_started_at = models.DateTimeField(null=True, blank=True)
    qc_passed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
