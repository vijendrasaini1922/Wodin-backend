import logging
import threading

from celery import shared_task
from django.conf import settings
from django.db import close_old_connections

from . import services
from .models import AIBook

log = logging.getLogger(__name__)


def _run(kind: str, book_id: str) -> None:
    book = AIBook.objects.select_related("user").filter(pk=book_id).first()
    if not book:
        return
    if kind == "questions":
        services.run_questions(book)
    elif kind == "generate":
        services.run_generation(book)


@shared_task
def run_aibook(kind: str, book_id: str) -> None:
    _run(kind, book_id)


def _run_in_thread(kind: str, book_id: str) -> None:
    try:
        _run(kind, book_id)
    except Exception:  # noqa: BLE001 - the service already records failures on the book
        log.exception("AI book background job failed")
    finally:
        close_old_connections()


def dispatch(kind: str, book_id: str) -> None:
    """Celery when Redis is configured; a daemon thread in dev (so the browser can poll progress); inline in tests."""
    if settings.AIBOOK_INLINE:
        _run(kind, book_id)
    elif settings.REDIS_URL:
        run_aibook.delay(kind, book_id)
    else:
        threading.Thread(target=_run_in_thread, args=(kind, book_id), daemon=True).start()
