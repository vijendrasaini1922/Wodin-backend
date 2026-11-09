"""Production gate and workflow. Production may only start when BOTH hold:
  1. the exact, latest revision is approved by the customer (valid, not superseded), and
  2. payment is verified server-side.
Approval and payment are separate records; this module is the single place that combines them."""
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.notifications import audit
from apps.orders import services as order_services
from apps.orders.models import Order
from apps.photos.storage import get_storage
from apps.projects.models import BookRevision
from apps.projects.services import latest_valid_approval

from . import printpdf
from .models import ProductionJob, default_checklist


def check_gate(order: Order) -> BookRevision:
    """Return the approved revision or raise ValidationError describing why production is blocked."""
    if order.payment_status not in (Order.PaymentStatus.PAID, Order.PaymentStatus.PARTIALLY_REFUNDED):
        raise ValidationError({"detail": "Payment has not been verified for this order."})
    project = order.items.get().project
    approval = latest_valid_approval(project)
    if not approval:
        raise ValidationError({"detail": "There is no valid customer approval for the current design."})
    latest = project.revisions.order_by("-number").first()
    if approval.revision_id != latest.id:
        raise ValidationError({"detail": "The approved revision has been superseded. Customer must re-approve."})
    return approval.revision


@transaction.atomic
def create_job(order: Order) -> ProductionJob:
    existing = ProductionJob.objects.filter(order=order).first()
    if existing:
        return existing
    check_gate(order)
    return ProductionJob.objects.create(order=order)


@transaction.atomic
def generate_pdf(job: ProductionJob) -> ProductionJob:
    job = ProductionJob.objects.select_for_update().get(pk=job.pk)
    if job.status != ProductionJob.Status.READY:
        raise ValidationError({"detail": "The print file can only be regenerated before printing starts."})
    revision = check_gate(job.order)
    storage = get_storage()
    variant = job.order.items.get().project.variant
    data, report = printpdf.build(revision, variant, storage)
    if data:
        job.print_pdf_key = f"production/{job.order_id}/rev{revision.number}/print.pdf"
        storage.save(job.print_pdf_key, data, "application/pdf")
    job.revision, job.pdf_report = revision, report
    job.pdf_valid = bool(data) and not report["errors"]
    job.save()
    return job


@transaction.atomic
def start_printing(job: ProductionJob, actor) -> ProductionJob:
    job = ProductionJob.objects.select_for_update().get(pk=job.pk)
    if job.status != ProductionJob.Status.READY:
        raise ValidationError({"detail": "Printing has already started."})
    revision = check_gate(job.order)  # re-check: approval may have been invalidated since the job was created
    if not job.pdf_valid or job.revision_id != revision.id:
        raise ValidationError({"detail": "Generate and validate the print-ready PDF for the approved revision first."})
    job.status, job.printing_started_at = ProductionJob.Status.PRINTING, timezone.now()
    job.save(update_fields=["status", "printing_started_at"])
    audit.log("production.started", actor=actor, obj=job, order=job.order.number, revision=revision.number)
    order_services.set_status(job.order, Order.Status.PRINTING, actor=actor, note="Printing started")
    return job


@transaction.atomic
def move_to_quality_check(job: ProductionJob, actor) -> ProductionJob:
    job = ProductionJob.objects.select_for_update().get(pk=job.pk)
    if job.status != ProductionJob.Status.PRINTING:
        raise ValidationError({"detail": "Printing must be in progress."})
    job.status, job.checklist = ProductionJob.Status.QUALITY_CHECK, default_checklist()
    job.save(update_fields=["status", "checklist"])
    order_services.set_status(job.order, Order.Status.QUALITY_CHECK, actor=actor, note="Printing complete")
    return job


@transaction.atomic
def record_qc(job: ProductionJob, checklist: dict, notes: str, actor) -> ProductionJob:
    job = ProductionJob.objects.select_for_update().get(pk=job.pk)
    if job.status not in (ProductionJob.Status.QUALITY_CHECK, ProductionJob.Status.PASSED):
        raise ValidationError({"detail": "The order is not in quality check."})
    unknown = set(checklist) - set(default_checklist())
    if unknown:
        raise ValidationError({"checklist": f"Unknown checklist items: {sorted(unknown)}"})
    job.checklist = {**job.checklist, **{k: bool(v) for k, v in checklist.items()}}
    job.qc_notes, job.qc_by = notes, actor
    if all(job.checklist.values()):
        job.status, job.qc_passed_at = ProductionJob.Status.PASSED, timezone.now()
        audit.log("production.qc_passed", actor=actor, obj=job, order=job.order.number)
    else:
        job.status, job.qc_passed_at = ProductionJob.Status.QUALITY_CHECK, None
    job.save()
    return job


@transaction.atomic
def fail_qc(job: ProductionJob, notes: str, actor) -> ProductionJob:
    """Send a book back to reprint."""
    job = ProductionJob.objects.select_for_update().get(pk=job.pk)
    if job.status not in (ProductionJob.Status.QUALITY_CHECK, ProductionJob.Status.PASSED):
        raise ValidationError({"detail": "The order is not in quality check."})
    job.status, job.qc_notes, job.qc_passed_at = ProductionJob.Status.PRINTING, notes, None
    job.checklist = default_checklist()
    job.save()
    order_services.set_status(job.order, Order.Status.PRINTING, actor=actor, note=f"QC failed: {notes}"[:300])
    return job
