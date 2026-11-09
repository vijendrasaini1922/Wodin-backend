from celery import shared_task

from .models import Photo
from .processing import PhotoRejected, process
from .storage import get_storage


@shared_task
def process_photo(photo_id: str) -> None:
    photo = Photo.objects.select_related("trip").filter(pk=photo_id).first()
    if not photo or photo.status not in (Photo.Status.UPLOADED, Photo.Status.READY):
        return
    storage = get_storage()
    try:
        process(photo, storage)
    except PhotoRejected as exc:
        photo.status, photo.reject_reason = Photo.Status.REJECTED, str(exc)
    except Exception:
        photo.status, photo.reject_reason = Photo.Status.REJECTED, "Could not process this file."
    photo.save()
