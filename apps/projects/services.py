"""Revision / approval domain rules."""
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.notifications import audit

from .models import Approval, BookProject, BookRevision, ChangeRequest


def build_snapshot(project: BookProject) -> dict:
    pages = []
    for page in project.pages.prefetch_related("elements"):
        pages.append({
            "number": page.number,
            "layout": page.layout,
            "elements": [
                {"kind": e.kind, "photo_id": str(e.photo_id) if e.photo_id else None, "text": e.text,
                 "x": e.x, "y": e.y, "w": e.w, "h": e.h, "z": e.z, "style": e.style}
                for e in page.elements.all()
            ],
        })
    return {"page_count": project.page_count, "pages": pages}


def latest_valid_approval(project: BookProject) -> Approval | None:
    return (Approval.objects.filter(revision__project=project, invalidated_at__isnull=True)
            .order_by("-revision__number").first())


@transaction.atomic
def publish_revision(project: BookProject, designer, notes: str = "") -> BookRevision:
    """Snapshot the current layout as a new revision. Supersedes older revisions and invalidates approvals."""
    project = BookProject.objects.select_for_update().get(pk=project.pk)
    if project.status == BookProject.Status.DRAFT:
        raise ValidationError({"detail": "The customer has not submitted this project yet."})
    if not project.pages.exists():
        raise ValidationError({"detail": "Add at least one page before sending a preview."})
    last = project.revisions.order_by("-number").first()
    now = timezone.now()
    if last:
        project.revisions.exclude(status=BookRevision.Status.SUPERSEDED).update(status=BookRevision.Status.SUPERSEDED)
        Approval.objects.filter(revision__project=project, invalidated_at__isnull=True).update(
            invalidated_at=now, invalidated_reason=f"Design changed in revision {last.number + 1}")
    revision = BookRevision.objects.create(
        project=project, number=(last.number + 1) if last else 1, snapshot=build_snapshot(project),
        notes=notes, created_by=designer)
    ChangeRequest.objects.filter(revision__project=project, resolved=False).update(resolved=True)
    project.status = BookProject.Status.PREVIEW_READY
    project.save(update_fields=["status", "updated_at"])
    audit.log("revision.published", actor=designer, obj=revision, project=project.pk, number=revision.number)
    return revision


@transaction.atomic
def approve_revision(project: BookProject, revision_id, user, ip=None) -> Approval:
    project = BookProject.objects.select_for_update().get(pk=project.pk)
    revision = project.revisions.filter(pk=revision_id).first()
    if not revision:
        raise ValidationError({"revision_id": "Unknown revision."})
    if revision.status != BookRevision.Status.SENT or revision != project.revisions.order_by("-number").first():
        raise ValidationError({"revision_id": "This version is no longer current. Please review the latest preview."})
    approval = Approval.objects.create(revision=revision, user=user, ip_address=ip)
    revision.status = BookRevision.Status.APPROVED
    revision.save(update_fields=["status"])
    project.status = BookProject.Status.APPROVED
    project.save(update_fields=["status", "updated_at"])
    audit.log("revision.approved", actor=user, obj=approval, ip=ip, project=project.pk, revision=revision.pk,
              revision_number=revision.number)
    return approval
