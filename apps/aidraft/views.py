from datetime import timedelta

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from django.conf import settings

from apps.core.permissions import IsDesigner
from apps.projects.models import BookProject

from . import layouts
from .models import AIDraftJob
from .tasks import run_ai_draft

STALE_AFTER = timedelta(minutes=15)


def _payload(job: AIDraftJob) -> dict:
    data = {"id": str(job.id), "status": job.status, "error": job.error, "created_at": job.created_at}
    if job.status == AIDraftJob.Status.DONE:
        data.update(pages=job.pages, summary=job.summary, model=job.model, photos_sent=job.photos_sent)
    return data


def _project(pk) -> BookProject:
    return get_object_or_404(BookProject.objects.exclude(status=BookProject.Status.DRAFT), pk=pk)


class StartAIDraftView(APIView):
    permission_classes = [IsDesigner]

    def post(self, request, project_id):
        project = _project(project_id)
        if project.status == BookProject.Status.APPROVED:
            raise ValidationError({"detail": "This design is already approved. Create changes through a new revision first."})
        # One run at a time: a double click returns the run that is already going.
        active = project.ai_drafts.filter(status__in=[AIDraftJob.Status.QUEUED, AIDraftJob.Status.RUNNING]).first()
        if active:
            if timezone.now() - active.created_at < STALE_AFTER:
                return Response(_payload(active), status=status.HTTP_202_ACCEPTED)
            active.status, active.error = AIDraftJob.Status.FAILED, "The previous run timed out."
            active.save(update_fields=["status", "error"])
        if project.ai_drafts.count() >= settings.AI_MAX_RUNS_PER_PROJECT:
            raise ValidationError({"detail": f"AI draft limit reached for this book ({settings.AI_MAX_RUNS_PER_PROJECT} runs)."})
        job = AIDraftJob.objects.create(project=project, requested_by=request.user)
        run_ai_draft.delay(str(job.id))  # runs inline in dev (no Redis), in a worker in production
        job.refresh_from_db()
        return Response(_payload(job), status=status.HTTP_202_ACCEPTED)


class AIDraftStatusView(APIView):
    permission_classes = [IsDesigner]

    def get(self, request, project_id, job_id):
        job = get_object_or_404(AIDraftJob, pk=job_id, project=_project(project_id))
        return Response(_payload(job))


class LayoutLibraryView(APIView):
    """Page layouts for the designer editor, adapted to the book's shape (?aspect=width/height)."""

    permission_classes = [IsDesigner]

    def get(self, request):
        try:
            aspect = min(2.0, max(0.5, float(request.query_params.get("aspect", layouts.DEFAULT_ASPECT))))
        except ValueError:
            aspect = layouts.DEFAULT_ASPECT
        return Response(layouts.all_layouts(aspect))
