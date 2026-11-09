from celery import shared_task

from .models import AIDraftJob
from .services import run_job


@shared_task
def run_ai_draft(job_id: str) -> None:
    job = AIDraftJob.objects.select_related("project__trip").filter(pk=job_id, status=AIDraftJob.Status.QUEUED).first()
    if job:
        run_job(job)
