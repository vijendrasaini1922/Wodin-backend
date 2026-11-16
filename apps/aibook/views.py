import uuid
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsDesigner
from apps.notifications import audit
from apps.photos import processing
from apps.photos.serializers import UploadRequestSerializer
from apps.photos.storage import LocalStorage, get_storage

from . import services
from .models import AIBook, AIBookPhoto
from .tasks import dispatch

STALE_AFTER = timedelta(minutes=20)
BUSY = (AIBook.Status.ASKING, AIBook.Status.GENERATING)
PDF_SALT = "wodin.aibook.pdf"


# AI Book mode is a staff tool for now: designers (and admin) only. Each run costs money, so it is not open to customers.
IsSignedIn = IsDesigner


def _book(request, pk) -> AIBook:
    book = AIBook.objects.filter(pk=pk).first()
    if not book:
        raise NotFound()
    if book.user != request.user and not (request.user.is_staff or getattr(request.user, "is_designer", False) or request.user.is_superuser):
        raise NotFound()
    if book.status in BUSY and timezone.now() - book.updated_at > STALE_AFTER:  # a worker died: do not spin forever
        book.status, book.error, book.stage = AIBook.Status.FAILED, "This took too long. Please try again.", ""
        book.save(update_fields=["status", "error", "stage", "updated_at"])
    return book


def _url(key: str) -> str | None:
    return get_storage().download_url(key, settings.DOWNLOAD_URL_TTL_SECONDS) if key else None


def _detail(book: AIBook) -> dict:
    ready = book.status == AIBook.Status.READY
    order_num = None
    if book.project_id:
        try:
            if hasattr(book.project, "order_item"):
                order_num = book.project.order_item.number
        except Exception:
            pass
    data = {
        "id": str(book.id), "project_id": str(book.project_id) if book.project_id else None,
        "order_number": order_num,
        "title": book.title, "prompt": book.prompt, "size": book.size, "page_count": book.page_count,
        "allow_questions": book.allow_questions, "premium": book.premium, "status": book.status, "stage": book.stage, "progress": book.progress,
        "error": book.error, "questions": book.questions if book.status == AIBook.Status.QUESTIONS else [],
        "photo_count": book.photos.filter(status=AIBookPhoto.Status.READY).count(),
        "generations": book.generations, "max_generations": settings.AIBOOK_MAX_GENERATIONS_PER_BOOK,
        "aspect": services.aspect(book), "updated_at": book.updated_at, "created_at": book.created_at,
        "pages": [], "summary": {}, "pdf_size": 0,
    }
    if ready:
        s = book.summary or {}
        data.update(pages=[_url(k) for k in book.view_keys], pdf_size=book.pdf_size,
                    summary={k: s.get(k) for k in ("book_title", "pages_used", "photos_used", "photos_total", "photos_reused", "warnings", "dropped", "premium", "quotes_used", "map_stops",
                                                   "pdf_dpi", "bleed_mm", "trim_mm", "low_resolution_photos")})
    return data


def _photo(p: AIBookPhoto) -> dict:
    return {"id": str(p.id), "original_filename": p.original_filename, "status": p.status, "reject_reason": p.reject_reason,
            "flags": p.flags, "width": p.width, "height": p.height,
            "thumb_url": _url(p.thumb_key) if p.status == AIBookPhoto.Status.READY else None}


def _clean(v, n) -> str:
    return "".join(c for c in str(v or "") if c >= " " or c == "\n").strip()[:n]


class BookListView(APIView):
    permission_classes = [IsSignedIn]

    def get(self, request):
        return Response([{**_detail(b), "pages": [], "cover": _url(b.view_keys[0]) if b.status == AIBook.Status.READY and b.view_keys else None}
                         for b in AIBook.objects.filter(user=request.user)[:50]])

    def post(self, request):
        if AIBook.objects.filter(user=request.user).count() >= 100:
            raise ValidationError({"detail": "You have reached the limit of AI books. Delete some to make a new one."})
        book = AIBook(user=request.user)
        _apply(book, request.data, creating=True)
        book.save()
        return Response(_detail(book), status=status.HTTP_201_CREATED)


def _apply(book: AIBook, data, creating=False) -> None:
    errors = {}
    if "prompt" in data or creating:
        prompt = _clean(data.get("prompt"), 4000)
        if creating and len(prompt) < 10:
            errors["prompt"] = "Tell the AI a little about the book (at least a sentence)."
        book.prompt = prompt
    if "title" in data:
        book.title = _clean(data.get("title"), 120)
    if "size" in data:
        if data["size"] not in AIBook.Size.values:
            errors["size"] = "Choose portrait, square or landscape."
        elif book.project_id and data["size"] != book.size:
            errors["size"] = "This book follows the shape the customer chose, so it cannot be changed."
        else:
            book.size = data["size"]
    if "page_count" in data:
        pc = data["page_count"]
        if pc in (None, ""):
            book.page_count = None
        else:
            try:
                pc = int(pc)
            except (TypeError, ValueError):
                pc = 0
            if not 4 <= pc <= 60:
                errors["page_count"] = "Pages must be between 4 and 60."
            else:
                book.page_count = pc
    if "allow_questions" in data:
        book.allow_questions = bool(data["allow_questions"])
    if "premium" in data:
        book.premium = bool(data["premium"])
    if errors:
        raise ValidationError(errors)


class BookDetailView(APIView):
    permission_classes = [IsSignedIn]

    def get(self, request, pk):
        return Response(_detail(_book(request, pk)))

    def patch(self, request, pk):
        book = _book(request, pk)
        if book.status in BUSY:
            raise ValidationError({"detail": "The AI is working on this book. Wait until it finishes."})
        _apply(book, request.data)
        book.save()
        return Response(_detail(book))

    def delete(self, request, pk):
        book = _book(request, pk)
        if book.status in BUSY:
            raise ValidationError({"detail": "The AI is working on this book. Wait until it finishes."})
        services.delete_files(book)
        book.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class BookPagesView(APIView):
    permission_classes = [IsSignedIn]

    def get(self, request, pk):
        book = _book(request, pk)
        photos = [_photo(p) for p in book.photos.filter(status=AIBookPhoto.Status.READY)]
        return Response({"pages": book.pages or [], "photos": photos})

    def put(self, request, pk):
        book = _book(request, pk)
        if book.status in BUSY:
            raise ValidationError({"detail": "The AI is working on this book. Please wait."})
        pages = request.data
        if not isinstance(pages, list):
            raise ValidationError({"pages": "Pages must be a list of page objects."})
        book.pages = pages
        pdf_key, pdf_size, view_keys, report = services.render_book(book, pages)
        book.pdf_key, book.pdf_size, book.view_keys = pdf_key, pdf_size, view_keys
        if book.summary:
            book.summary.update(report)
        else:
            book.summary = report
        book.status = AIBook.Status.READY
        book.save()
        return Response(_detail(book))


class BookPhotosView(APIView):
    permission_classes = [IsSignedIn]

    def get(self, request, pk):
        book = _book(request, pk)
        return Response([_photo(p) for p in book.photos.exclude(status=AIBookPhoto.Status.PENDING)])


class UploadUrlsView(APIView):
    """Same signed-URL flow as trip photos: register files, PUT straight to storage, then call complete."""

    permission_classes = [IsSignedIn]

    def post(self, request, pk):
        book = _book(request, pk)
        if book.status in BUSY:
            raise ValidationError({"detail": "The AI is working on this book. Add photos after it finishes."})
        ser = UploadRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        files = ser.validated_data["files"]
        if book.photos.count() + len(files) > settings.AIBOOK_MAX_PHOTOS:
            raise ValidationError({"files": f"An AI book can have at most {settings.AIBOOK_MAX_PHOTOS} photos."})
        storage, results = get_storage(), []
        pos = book.photos.count()
        for f in files:
            ctype = processing.declared_type(f["filename"], f["content_type"])
            if not ctype:
                results.append({"filename": f["filename"], "error": "Unsupported file type. Use JPG, PNG or HEIC."})
            elif f["size"] > settings.MAX_UPLOAD_BYTES:
                results.append({"filename": f["filename"], "error": "File is too large."})
            else:
                pid = uuid.uuid4()
                key = f"{services.storage_prefix(book)}/photos/{pid}/original.{processing.EXT_BY_TYPE[ctype]}"
                AIBookPhoto.objects.create(id=pid, book=book, original_filename=f["filename"][:255], content_type=ctype, original_key=key, position=pos)
                pos += 1
                results.append({"filename": f["filename"], "photo_id": str(pid), "upload": storage.upload_url(key, ctype, settings.UPLOAD_URL_TTL_SECONDS)})
        return Response({"files": results})


class CompletePhotoView(APIView):
    permission_classes = [IsSignedIn]

    def post(self, request, photo_id):
        photo = AIBookPhoto.objects.select_related("book").filter(pk=photo_id, book__user=request.user).first()
        if not photo:
            raise NotFound()
        if photo.status == AIBookPhoto.Status.PENDING:
            if not get_storage().exists(photo.original_key):
                raise ValidationError({"detail": "Upload not found. Please retry the upload."})
            services.process_photo(photo)
        return Response(_photo(photo))


class PhotoDeleteView(APIView):
    permission_classes = [IsSignedIn]

    def delete(self, request, photo_id):
        photo = AIBookPhoto.objects.select_related("book").filter(pk=photo_id, book__user=request.user).first()
        if not photo:
            raise NotFound()
        if photo.book.status in BUSY:
            raise ValidationError({"detail": "The AI is working on this book."})
        storage = get_storage()
        for k in (photo.original_key, photo.render_key, photo.thumb_key):
            if k:
                storage.delete(k)
        photo.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class StartView(APIView):
    """Start (or restart) the AI. It may come back with questions first; answer them and generation continues."""

    permission_classes = [IsSignedIn]

    def post(self, request, pk):
        book = _book(request, pk)
        if book.status in BUSY:
            return Response(_detail(book), status=status.HTTP_202_ACCEPTED)
        if book.status == AIBook.Status.QUESTIONS:
            return Response(_detail(book), status=status.HTTP_202_ACCEPTED)
        if not book.photos.filter(status=AIBookPhoto.Status.READY).exists():
            raise ValidationError({"detail": "Add some photos first."})
        if book.generations >= settings.AIBOOK_MAX_GENERATIONS_PER_BOOK:
            raise ValidationError({"detail": f"This book has been regenerated {book.generations} times, which is the limit. Start a new book."})
        if services.runs_today(request.user) >= settings.AIBOOK_MAX_RUNS_PER_DAY:
            raise ValidationError({"detail": "You have reached today's AI limit. Please try again tomorrow."})
        asking = book.allow_questions and not book.answers and not book.questions
        book.status = AIBook.Status.ASKING if asking else AIBook.Status.GENERATING
        book.stage, book.progress, book.error = "Starting", 3, ""
        book.save(update_fields=["status", "stage", "progress", "error", "updated_at"])
        dispatch("questions" if asking else "generate", str(book.id))
        book.refresh_from_db()
        return Response(_detail(book), status=status.HTTP_202_ACCEPTED)


class AnswerView(APIView):
    permission_classes = [IsSignedIn]

    def post(self, request, pk):
        book = _book(request, pk)
        if book.status != AIBook.Status.QUESTIONS:
            raise ValidationError({"detail": "The AI is not waiting for answers."})
        ids = {q["id"] for q in book.questions}
        raw = request.data.get("answers") if isinstance(request.data.get("answers"), dict) else {}
        book.answers = {k: _clean(v, 500) for k, v in raw.items() if k in ids and _clean(v, 500)}
        book.status, book.stage, book.progress = AIBook.Status.GENERATING, "Starting", 14
        book.save(update_fields=["answers", "status", "stage", "progress", "updated_at"])
        dispatch("generate", str(book.id))
        book.refresh_from_db()
        return Response(_detail(book), status=status.HTTP_202_ACCEPTED)


class PdfLinkView(APIView):
    """A short-lived signed link to the PDF (`?inline=1` to open it in the browser instead of downloading)."""

    permission_classes = [IsSignedIn]

    def get(self, request, pk):
        book = _book(request, pk)
        if book.status != AIBook.Status.READY or not book.pdf_key:
            raise ValidationError({"detail": "The PDF is not ready yet."})
        inline = request.query_params.get("inline") == "1"
        name = "".join(c if c.isalnum() or c in "-_ " else "" for c in (book.title or "photobook")).strip().replace(" ", "-") or "photobook"
        storage = get_storage()
        if isinstance(storage, LocalStorage):
            token = signing.dumps({"k": book.pdf_key, "n": f"{name}.pdf", "i": inline}, salt=PDF_SALT)
            url = f"{settings.BACKEND_URL}/api/aibooks/pdf/{token}/"
        else:
            url = storage.download_url(book.pdf_key, settings.DOWNLOAD_URL_TTL_SECONDS)
        audit.log("aibook.pdf", actor=request.user, obj=book)
        return Response({"url": url, "filename": f"{name}.pdf", "size": book.pdf_size})


@require_GET
def local_pdf(request, token):
    try:
        data = signing.loads(token, salt=PDF_SALT, max_age=settings.DOWNLOAD_URL_TTL_SECONDS)
    except signing.BadSignature:
        return HttpResponse(status=403)
    storage = get_storage()
    if not isinstance(storage, LocalStorage) or not storage.exists(data["k"]):
        raise Http404()
    resp = FileResponse(open(storage.path_for(data["k"]), "rb"), content_type="application/pdf")
    resp["Content-Disposition"] = f'{"inline" if data.get("i") else "attachment"}; filename="{data["n"]}"'
    resp["Cache-Control"] = "private, no-store"
    return resp
