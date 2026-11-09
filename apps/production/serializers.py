from rest_framework import serializers

from .models import QC_ITEMS, ProductionJob


class JobSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source="order.number", read_only=True)
    checklist_labels = serializers.SerializerMethodField()
    revision_number = serializers.IntegerField(source="revision.number", read_only=True, default=None)

    class Meta:
        model = ProductionJob
        fields = ["id", "order", "order_number", "status", "revision_number", "pdf_valid", "pdf_report",
                  "checklist", "checklist_labels", "qc_notes", "created_at", "printing_started_at", "qc_passed_at"]

    def get_checklist_labels(self, obj):
        return dict(QC_ITEMS)
