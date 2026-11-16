"""AI Book pipeline: prepare photos -> (optional) ask questions -> describe the story -> plan pages -> render -> PDF.

Nothing here touches trips, projects, orders or payments.
"""
import gc
import hashlib
import io
import logging
import uuid
from datetime import datetime, timedelta, timezone as dt_tz

from django.conf import settings
from django.utils import timezone
from PIL import Image, ImageOps
from rest_framework.exceptions import ValidationError

from apps.aidraft.normalise import normalise_for_ai
from apps.aidraft.planner import PhotoInfo, build_book
from apps.aidraft.providers import AIProviderError, ImageInput, get_provider
from apps.aidraft.services import _chunks, _merge
from apps.notifications import audit
from apps.photos import processing
from apps.photos.storage import get_storage

from . import ai, render
from .models import AIBook, AIBookPhoto, AIBookRun

log = logging.getLogger(__name__)
_FAR = datetime.max.replace(tzinfo=dt_tz.utc)

# trim size in millimetres per book shape
TRIM_MM = {"portrait": (210.0, 297.0), "square": (210.0, 210.0), "landscape": (297.0, 210.0)}
RENDER_EDGE, THUMB_EDGE, VIEW_WIDTH = 2600, 400, 1100


def trim_mm(book: AIBook) -> tuple[float, float]:
    return TRIM_MM.get(book.size, TRIM_MM["portrait"])


def aspect(book: AIBook) -> float:
    w, h = trim_mm(book)
    return w / h


def project_size(project):
    """The shape the customer bought, from the variant's trim: landscape, square or portrait."""
    variant = getattr(project, "variant", None)
    if not (variant and variant.trim_height_mm and variant.trim_width_mm):
        return AIBook.Size.PORTRAIT
    ratio = float(variant.trim_width_mm) / float(variant.trim_height_mm)
    if ratio > 1.15:
        return AIBook.Size.LANDSCAPE
    if ratio < 0.85:
        return AIBook.Size.PORTRAIT
    return AIBook.Size.SQUARE


def storage_prefix(book: AIBook) -> str:
    return f"aibooks/{book.user_id}/{book.id}"


def create_from_project(
    project,
    user,
    prompt: str | None = None,
    title: str | None = None,
    page_count: int | None = None,
    allow_questions: bool = True,
    premium: bool = True,
    auto_start: bool = True,
) -> AIBook:
    """Create an AIBook directly from a customer's BookProject (trip + uploaded photos)."""
    from . import tasks

    trip = project.trip
    trip_photos = list(trip.photos.filter(status="ready").order_by("position", "taken_at", "created_at"))
    if not trip_photos:
        raise ValidationError({"detail": "This project has no ready photos to generate an AI book from."})

    book_size = project_size(project)

    book_title = (title or trip.cover_title or trip.title or "Travel Memories").strip()
    if not prompt:
        prompt_parts = []
        if trip.description:
            prompt_parts.append(trip.description.strip())
        if trip.destinations:
            places = ", ".join(str(d) for d in trip.destinations if d)
            if places:
                prompt_parts.append(f"Destinations: {places}")
        if trip.companions:
            folks = ", ".join(str(c) for c in trip.companions if c)
            if folks:
                prompt_parts.append(f"Travelled with: {folks}")
        if trip.start_date or trip.end_date:
            dates = []
            if trip.start_date:
                dates.append(str(trip.start_date))
            if trip.end_date:
                dates.append(str(trip.end_date))
            prompt_parts.append(f"Dates: {' to '.join(dates)}")
        if trip.special_message:
            prompt_parts.append(f"Special message: {trip.special_message.strip()}")
        if trip.dedication:
            prompt_parts.append(f"Dedication: {trip.dedication.strip()}")

        book_prompt = "\n".join(prompt_parts).strip()
        if len(book_prompt) < 10:
            book_prompt = f"Travel photobook for {book_title}. Create an art-directed story of our journey."
    else:
        book_prompt = str(prompt).strip()

    if page_count is not None and str(page_count).isdigit():
        pages_num = int(page_count)
    elif page_count is None:
        pages_num = project.page_count
    else:
        pages_num = None

    book = AIBook.objects.create(
        user=user,
        project=project,
        title=book_title[:120],
        prompt=book_prompt[:4000],
        size=book_size,
        page_count=pages_num,
        allow_questions=allow_questions,
        premium=premium,
    )

    storage = get_storage()
    if len(trip_photos) > settings.AIBOOK_MAX_PHOTOS:
        trip_photos = trip_photos[: settings.AIBOOK_MAX_PHOTOS]

    for pos, p in enumerate(trip_photos):
        pid = uuid.uuid4()
        ext = processing.EXT_BY_TYPE.get(p.content_type, "jpg")
        target_key = f"{storage_prefix(book)}/photos/{pid}/original.{ext}"
        try:
            raw_bytes = storage.read(p.original_key)
            storage.save(target_key, raw_bytes, p.content_type)
        except Exception:
            log.exception("Could not copy photo %s for AI book %s", p.id, book.id)
            continue
        ai_photo = AIBookPhoto.objects.create(
            id=pid,
            book=book,
            trip_photo_id=p.id,
            original_filename=p.original_filename,
            content_type=p.content_type,
            original_key=target_key,
            position=pos,
        )
        process_photo(ai_photo)

    if auto_start:
        if book.allow_questions:
            tasks.dispatch("questions", str(book.id))
        else:
            tasks.dispatch("generate", str(book.id))

    return book


# ------------------------------------------------------------------------------------------------
# photos
# ------------------------------------------------------------------------------------------------
def process_photo(photo: AIBookPhoto) -> None:
    """Validate the uploaded original and make the working copies. Never modifies the original."""
    storage = get_storage()
    data = storage.read(photo.original_key)
    try:
        if len(data) > settings.MAX_UPLOAD_BYTES:
            raise processing.PhotoRejected("File is too large.")
        try:
            with Image.open(io.BytesIO(data)) as probe:
                fmt = probe.format
                probe.verify()
            img = Image.open(io.BytesIO(data))
            img.load()
        except Exception:  # noqa: BLE001
            raise processing.PhotoRejected("File is corrupted or not a supported image.")
        if fmt not in processing.ALLOWED_FORMATS or photo.content_type not in processing.ALLOWED_FORMATS[fmt]:
            raise processing.PhotoRejected("File content does not match its type. Use JPG, PNG or HEIC.")
        oriented = ImageOps.exif_transpose(img)
        w, h = oriented.size
        if min(w, h) < settings.REJECT_PHOTO_PIXELS:
            raise processing.PhotoRejected("Image resolution is too low to print.")
        flags = []
        if min(w, h) < settings.MIN_PHOTO_PIXELS:
            flags.append("low_resolution")
        if processing.blur_score(oriented) < settings.BLUR_THRESHOLD:
            flags.append("blurry")
        digest, hash_ = hashlib.sha256(data).hexdigest(), processing.dhash(oriented)
        for other in photo.book.photos.filter(status=AIBookPhoto.Status.READY).exclude(pk=photo.pk):
            if other.sha256 == digest or (other.dhash and processing.hamming(other.dhash, hash_) <= settings.NEAR_DUPLICATE_DISTANCE):
                flags.append("duplicate")
                break
        base = photo.original_key.rsplit("/", 1)[0]
        photo.render_key, photo.thumb_key = f"{base}/render.jpg", f"{base}/thumb.jpg"
        rgb = _to_srgb(oriented, img.info.get("icc_profile"))
        storage.save(photo.render_key, processing._jpeg_bytes(rgb, RENDER_EDGE, 90), "image/jpeg")
        storage.save(photo.thumb_key, processing._jpeg_bytes(rgb, THUMB_EDGE, 80), "image/jpeg")
        photo.width, photo.height = w, h
        photo.taken_at = processing._taken_at(img)
        photo.sha256, photo.dhash, photo.flags = digest, hash_, flags
        photo.status = AIBookPhoto.Status.READY
        photo.reject_reason = ""
    except processing.PhotoRejected as exc:
        photo.status, photo.reject_reason = AIBookPhoto.Status.REJECTED, str(exc)[:200]
    photo.save()


def _to_srgb(img: Image.Image, icc: bytes | None) -> Image.Image:
    from PIL import ImageCms

    img = img.convert("RGB")
    if icc:
        try:
            img = ImageCms.profileToProfile(img, ImageCms.ImageCmsProfile(io.BytesIO(icc)), ImageCms.createProfile("sRGB"), outputMode="RGB")
        except Exception:  # noqa: BLE001 - a broken profile must not lose the photo
            pass
    return img


def usable_photos(book: AIBook) -> list[AIBookPhoto]:
    qs = [p for p in book.photos.filter(status=AIBookPhoto.Status.READY) if "duplicate" not in (p.flags or [])]
    return sorted(qs, key=lambda p: (p.taken_at is None, p.taken_at or _FAR, p.position, p.created_at))


def _hint(p: AIBookPhoto) -> str:
    bits = []
    if "blurry" in (p.flags or []):
        bits.append("blurry")
    if p.taken_at:
        bits.append(f"taken {p.taken_at:%Y-%m-%d %H:%M}")
    return ", ".join(bits)


def _ai_images(photos: list[AIBookPhoto]) -> list[ImageInput]:
    storage = get_storage()
    out = []
    for p in photos:
        jpeg, _ = normalise_for_ai(storage.read(p.render_key), edge=settings.AI_IMAGE_EDGE)
        out.append(ImageInput(jpeg, _hint(p)))
    return out


def _sample(photos: list[AIBookPhoto], n: int = 6) -> list[AIBookPhoto]:
    if len(photos) <= n:
        return photos
    step = (len(photos) - 1) / (n - 1)
    return [photos[round(i * step)] for i in range(n)]


def _brief(book: AIBook) -> dict:
    return {"brief": book.prompt[:4000], "title": book.title, "answers": {k: str(v)[:500] for k, v in (book.answers or {}).items()},
            "questions": {q["id"]: q["question"] for q in (book.questions or [])}}


def _set(book: AIBook, **fields) -> None:
    for k, v in fields.items():
        setattr(book, k, v)
    book.save(update_fields=[*fields.keys(), "updated_at"])


# ------------------------------------------------------------------------------------------------
# step 1: questions
# ------------------------------------------------------------------------------------------------
def run_questions(book: AIBook) -> None:
    """Let the AI read the brief and a few photos, and ask up to 3 questions. If it has none, go straight on to the build."""
    _set(book, status=AIBook.Status.ASKING, stage="Reading your brief", progress=5, error="")
    try:
        photos = usable_photos(book)
        if not photos:
            raise AIProviderError("There are no usable photos to build from.")
        questions, tin, tout = ai.ask_questions(_ai_images(_sample(photos)), _brief(book))
        AIBookRun.objects.create(book=book, user=book.user, model=settings.GEMINI_MODEL, photos=min(6, len(photos)), tokens_in=tin, tokens_out=tout, ok=True)
    except AIProviderError as exc:
        _set(book, status=AIBook.Status.FAILED, error=str(exc)[:200], stage="")
        return
    except Exception:  # noqa: BLE001
        log.exception("AI book %s: question step failed", book.id)
        _set(book, status=AIBook.Status.FAILED, error="Could not start. Please try again.", stage="")
        return
    if questions:
        _set(book, status=AIBook.Status.QUESTIONS, questions=questions, answers={}, stage="Waiting for your answers", progress=10)
    else:
        _set(book, questions=[], stage="Starting", progress=12)
        run_generation(book)


# ------------------------------------------------------------------------------------------------
# step 2: generation
# ------------------------------------------------------------------------------------------------
def choose_page_count(n_photos: int, requested: int | None) -> int:
    if requested:
        return max(4, min(60, requested))
    return max(8, min(40, round(n_photos * 1.1)))


def run_generation(book: AIBook) -> None:
    _set(book, status=AIBook.Status.GENERATING, stage="Describing your photos", progress=15, error="", generations=book.generations + 1)
    tin = tout = 0
    try:
        photos = usable_photos(book)
        if not photos:
            raise AIProviderError("There are no usable photos to build from.")
        if len(photos) > settings.AIBOOK_MAX_PHOTOS:
            photos = photos[: settings.AIBOOK_MAX_PHOTOS]
        page_count = choose_page_count(len(photos), book.page_count)
        provider = get_provider()
        context = {**_brief(book), "target_scenes": 0, "premium": bool(book.premium)}
        scene_pages = max(1, page_count - 4)

        results, sizes = [], []
        chunks = _chunks(photos, settings.AI_CHUNK_SIZE)
        for idx, chunk in enumerate(chunks, 1):
            _set(book, stage=f"Describing your photos ({idx} of {len(chunks)})" if len(chunks) > 1 else "Describing your photos",
                 progress=15 + int(35 * (idx - 1) / len(chunks)))
            target = max(1, min(len(chunk), round(scene_pages * len(chunk) / len(photos))))
            part = {**context, "target_scenes": target}
            if len(chunks) > 1:
                part["part"] = f"{idx} of {len(chunks)}"
            res = provider.plan_story(_ai_images(chunk), part)
            results.append(res.story)
            sizes.append(len(chunk))
            tin, tout = tin + res.tokens_in, tout + res.tokens_out

        _set(book, stage="Designing the pages", progress=55)
        story = _merge(results, sizes)
        infos = [PhotoInfo(id=str(p.id), width=p.width, height=p.height, flags=[f for f in (p.flags or []) if f != "duplicate"],
                           taken=p.taken_at.date() if p.taken_at else None) for p in photos]
        facts = story.get("facts") or {}
        trip = {"title": book.title or story["book_title"], "cover_title": book.title, "destinations": facts.get("places") or [],
                "companions": facts.get("travellers") or [], "dates_text": facts.get("dates") or "", "special_message": "", "dedication": ""}
        pages, summary = build_book(infos, story, page_count, trip, aspect(book), borderless=True, premium=bool(book.premium))
        summary["page_size"] = book.size

        _set(book, stage="Printing the pages", progress=60)
        pdf_key, pdf_size, view_keys, report = render_book(book, pages)
        summary.update(report)
        book.pages, book.summary, book.pdf_key, book.pdf_size, book.view_keys = pages, summary, pdf_key, pdf_size, view_keys
        book.title = book.title or summary.get("book_title", "")[:120]
        book.status, book.stage, book.progress = AIBook.Status.READY, "Done", 100
        book.save()
        AIBookRun.objects.create(book=book, user=book.user, model=provider.model, photos=len(photos), tokens_in=tin, tokens_out=tout, ok=True)
        audit.log("aibook.generated", actor=book.user, obj=book, photos=len(photos), pages=len(pages), model=provider.model, tokens_in=tin, tokens_out=tout)
    except AIProviderError as exc:
        AIBookRun.objects.create(book=book, user=book.user, photos=0, tokens_in=tin, tokens_out=tout, ok=False)
        _set(book, status=AIBook.Status.FAILED, error=str(exc)[:200], stage="", progress=0)
    except Exception:  # noqa: BLE001 - never show internals; the traceback goes to the log
        log.exception("AI book %s failed", book.id)
        AIBookRun.objects.create(book=book, user=book.user, photos=0, tokens_in=tin, tokens_out=tout, ok=False)
        _set(book, status=AIBook.Status.FAILED, error="Could not build the book. Please try again.", stage="", progress=0)


def render_book(book: AIBook, pages: list[dict]) -> tuple[str, int, list[str], dict]:
    """Draw every page, upload web copies for the viewer, and build the PDF. Memory stays flat: one page at a time."""
    storage = get_storage()
    tw_mm, th_mm = trim_mm(book)
    dpi = settings.AIBOOK_PDF_DPI
    px = lambda mm: round(mm / render.MM_PER_INCH * dpi)  # noqa: E731
    trim_px, bleed_px = (px(tw_mm), px(th_mm)), px(settings.AIBOOK_BLEED_MM)

    cache: dict[str, Image.Image] = {}
    owned = {str(p.id): p for p in book.photos.filter(status=AIBookPhoto.Status.READY)}

    def load(photo_id: str):
        if photo_id in cache:
            return cache[photo_id]
        rec = owned.get(photo_id)
        if not rec:
            return None
        img = Image.open(io.BytesIO(storage.read(rec.render_key)))
        img.load()
        if len(cache) >= 6:  # a photo is rarely used on more than a few nearby pages
            cache.pop(next(iter(cache)))
        cache[photo_id] = img
        return img

    renderer = render.PageRenderer(trim_px[0], trim_px[1], bleed_px, load)
    prefix = storage_prefix(book)
    view_keys, parts, low = [], [], []
    for i, page in enumerate(pages):
        img = renderer.render(page)
        parts.append(render.page_pdf(img, dpi))
        view = img.crop((bleed_px, bleed_px, bleed_px + trim_px[0], bleed_px + trim_px[1]))
        view.thumbnail((VIEW_WIDTH, int(VIEW_WIDTH * 2)), Image.Resampling.LANCZOS)
        key = f"{prefix}/pages/{i:03d}.jpg"
        storage.save(key, render.to_jpeg(view, 85), "image/jpeg")
        view_keys.append(key)
        _set(book, progress=60 + int(38 * (i + 1) / len(pages)), stage=f"Printing page {i + 1} of {len(pages)}")
        del img, view
        gc.collect()
    for page in pages:  # report photos that are too small for the page they sit on
        for e in page["elements"]:
            rec = owned.get(e["photo"]) if e["kind"] == "photo" else None
            if rec and rec.width:
                eff = rec.width / (e["w"] * tw_mm / render.MM_PER_INCH)  # pixels per inch the photo gets on paper
                if eff < 150:
                    low.append(str(rec.id))
    data = render.merge_pdfs(parts, dpi, bleed_px)
    pdf_key = f"{prefix}/book.pdf"
    storage.save(pdf_key, data, "application/pdf")
    return pdf_key, len(data), view_keys, {"pdf_dpi": dpi, "bleed_mm": settings.AIBOOK_BLEED_MM,
                                           "trim_mm": [tw_mm, th_mm], "low_resolution_photos": sorted(set(low))}


def delete_files(book: AIBook) -> None:
    storage = get_storage()
    keys = [book.pdf_key, *(book.view_keys or [])]
    for p in book.photos.all():
        keys += [p.original_key, p.render_key, p.thumb_key]
    for k in keys:
        if k:
            try:
                storage.delete(k)
            except Exception:  # noqa: BLE001 - best effort: the database row is going away anyway
                log.warning("Could not delete %s", k)


def runs_today(user) -> int:
    return AIBookRun.objects.filter(user=user, created_at__gte=timezone.now() - timedelta(hours=24)).count()

