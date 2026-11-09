import uuid

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.db.models import Max
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.trips.models import Trip

from . import processing
from .models import Photo, PhotoGroup
from .serializers import PhotoGroupSerializer, PhotoSerializer, UploadRequestSerializer
from .storage import DOWNLOAD_SALT, UPLOAD_SALT, LocalStorage, get_storage
from .tasks import process_photo


def _trip_or_404(request, trip_id) -> Trip:
    try:
        return Trip.objects.get(pk=trip_id, user=request.user)
    except (Trip.DoesNotExist, ValueError, ValidationError):
        raise NotFound()


class TripPhotosView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, trip_id):
        trip = _trip_or_404(request, trip_id)
        photos = trip.photos.exclude(status=Photo.Status.PENDING)
        return Response(PhotoSerializer(photos, many=True).data)


class UploadUrlsView(APIView):
    """Step 1: register files and receive short-lived signed URLs to upload directly to storage."""

    permission_classes = [IsAuthenticated]

    def post(self, request, trip_id):
        trip = _trip_or_404(request, trip_id)
        ser = UploadRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        files = ser.validated_data["files"]
        if trip.photos.count() + len(files) > settings.MAX_PHOTOS_PER_TRIP:
            raise ValidationError({"files": f"A trip can have at most {settings.MAX_PHOTOS_PER_TRIP} photos."})

        storage, ttl = get_storage(), settings.UPLOAD_URL_TTL_SECONDS
        results, accepted = [], []
        next_pos = (trip.photos.aggregate(m=Max("position"))["m"] or 0) + 1
        for f in files:
            ctype = processing.declared_type(f["filename"], f["content_type"])
            if not ctype:
                results.append({"filename": f["filename"], "error": "Unsupported file type. Use JPG, PNG or HEIC."})
            elif f["size"] > settings.MAX_UPLOAD_BYTES:
                results.append({"filename": f["filename"], "error": "File is too large."})
            else:
                accepted.append((f, ctype))
        with transaction.atomic():
            for f, ctype in accepted:
                pid = uuid.uuid4()
                key = f"users/{request.user.id}/trips/{trip.id}/photos/{pid}/original.{processing.EXT_BY_TYPE[ctype]}"
                photo = Photo.objects.create(id=pid, user=request.user, trip=trip, original_filename=f["filename"][:255],
                                             content_type=ctype, original_key=key, position=next_pos)
                next_pos += 1
                results.append({"filename": f["filename"], "photo_id": str(photo.id),
                                "upload": storage.upload_url(key, ctype, ttl)})
        return Response({"files": results})


class CompleteUploadView(APIView):
    """Step 2: after the browser finishes the direct upload, verify it exists and process it."""

    permission_classes = [IsAuthenticated]

    def post(self, request, photo_id):
        photo = Photo.objects.filter(pk=photo_id, user=request.user).first()
        if not photo:
            raise NotFound()
        if photo.status == Photo.Status.PENDING:
            storage = get_storage()
            if not storage.exists(photo.original_key):
                raise ValidationError({"detail": "Upload not found. Please retry the upload."})
            photo.status = Photo.Status.UPLOADED
            photo.save(update_fields=["status"])
            process_photo.delay(str(photo.id))
            photo.refresh_from_db()
        return Response(PhotoSerializer(photo).data)


class PhotoViewSet(mixins.RetrieveModelMixin, mixins.UpdateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet):
    serializer_class = PhotoSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        return Photo.objects.filter(user=self.request.user).exclude(status=Photo.Status.PENDING)

    def perform_update(self, serializer):
        before = (serializer.instance.rotation, serializer.instance.crop)
        photo = serializer.save()
        if (photo.rotation, photo.crop) != before:  # re-render derivatives from the untouched original
            processing.build_derivatives(photo, get_storage())
            photo.save(update_fields=["thumb_key", "preview_key"])

    def perform_destroy(self, instance):
        storage = get_storage()
        for key in (instance.original_key, instance.thumb_key, instance.preview_key):
            if key:
                storage.delete(key)
        instance.delete()

    @action(detail=False, methods=["post"], url_path=r"reorder/(?P<trip_id>[^/.]+)")
    def reorder(self, request, trip_id=None):
        trip = _trip_or_404(request, trip_id)
        ids = request.data.get("ids")
        if not isinstance(ids, list):
            raise ValidationError({"ids": "Provide an ordered list of photo ids."})
        owned = {str(p.id): p for p in trip.photos.all()}
        if any(i not in owned for i in ids):
            raise ValidationError({"ids": "Unknown photo id for this trip."})
        with transaction.atomic():
            for pos, pid in enumerate(ids, start=1):
                Photo.objects.filter(pk=pid).update(position=pos)
        return Response({"ok": True})


class PhotoGroupViewSet(viewsets.ModelViewSet):
    serializer_class = PhotoGroupSerializer
    pagination_class = None

    def _trip(self):
        return _trip_or_404(self.request, self.kwargs["trip_id"])

    def get_queryset(self):
        return PhotoGroup.objects.filter(trip=self._trip())

    def perform_create(self, serializer):
        serializer.save(trip=self._trip())


@csrf_exempt
@require_http_methods(["PUT"])
def local_upload(request, token):
    """Local-storage equivalent of an S3 pre-signed PUT. The signed, expiring token is the only credential."""
    try:
        data = signing.loads(token, salt=UPLOAD_SALT, max_age=settings.UPLOAD_URL_TTL_SECONDS)
    except signing.BadSignature:
        return JsonResponse({"detail": "Invalid or expired upload URL."}, status=403)
    if int(request.META.get("CONTENT_LENGTH") or 0) > settings.MAX_UPLOAD_BYTES:
        return JsonResponse({"detail": "File is too large."}, status=413)
    body = request.body
    if len(body) > settings.MAX_UPLOAD_BYTES:
        return JsonResponse({"detail": "File is too large."}, status=413)
    get_storage().save(data["k"], body, data.get("ct", ""))
    return JsonResponse({"ok": True})


def local_download(request, token):
    try:
        data = signing.loads(token, salt=DOWNLOAD_SALT, max_age=settings.DOWNLOAD_URL_TTL_SECONDS)
    except signing.BadSignature:
        return HttpResponse(status=403)
    storage = get_storage()
    if not isinstance(storage, LocalStorage) or not storage.exists(data["k"]):
        raise Http404()
    return FileResponse(open(storage.path_for(data["k"]), "rb"), content_type="image/jpeg" if data["k"].endswith(".jpg") else "application/octet-stream")
