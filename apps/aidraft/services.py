"""Run an AI draft: normalise photos -> ask the provider for the story -> plan pages deterministically."""
import logging
from datetime import datetime, timezone as dt_tz

from django.conf import settings
from django.utils import timezone

from apps.notifications import audit
from apps.photos.models import Photo
from apps.photos.storage import get_storage

from .models import AIDraftJob
from .normalise import normalise_for_ai
from .planner import PhotoInfo, build_book, sanitise_story
from .providers import AIProviderError, ImageInput, get_provider

log = logging.getLogger(__name__)
_FAR = datetime.max.replace(tzinfo=dt_tz.utc)


def eligible_photos(project) -> list[Photo]:
    """Every usable photo of the trip, in the order they were taken. Exact duplicates stay in the library only."""
    qs = project.trip.photos.filter(status=Photo.Status.READY).exclude(preview_key="")
    # filtered in Python: JSON "contains" lookups are not available on SQLite
    qs = [p for p in qs if not ({"duplicate", "near_duplicate"} & set(p.flags or []))]
    return sorted(qs, key=lambda p: (p.taken_at is None, p.taken_at or _FAR, p.position, p.created_at))


def _hint(p: Photo) -> str:
    bits = []
    if p.is_favourite:
        bits.append("customer favourite")
    if "blurry" in p.flags:
        bits.append("blurry")
    if p.taken_at:
        bits.append(f"taken {p.taken_at:%Y-%m-%d %H:%M}")
    return ", ".join(bits)


def _chunks(items: list, size: int) -> list[list]:
    """Sizes differ by at most one (10 photos / max 4 -> 4,3,3), so no part is left too small to tell a story."""
    parts = max(1, -(-len(items) // size))
    base, extra = divmod(len(items), parts)
    out, i = [], 0
    for k in range(parts):
        n = base + (1 if k < extra else 0)
        out.append(items[i:i + n])
        i += n
    return out


def _merge(results: list[dict], sizes: list[int]) -> dict:
    """Combine per-chunk stories into one, shifting photo numbers so they index the whole album."""
    merged = {"book_title": "", "subtitle": "", "closing_title": "", "closing_line": "", "best_for_cover": None,
              "best_for_closing": None, "journey_intro": {"title": "", "story": "", "footer": ""},
              "group": {"title": "", "story": "", "footer": "", "photos": []}, "highlights": [], "scenes": [], "photos": {},
              "facts": {"places": [], "travellers": [], "dates": ""}, "quotes": [], "route": []}
    offset = 0
    results_s = [sanitise_story(r, sz) for r, sz in zip(results, sizes)]
    for i, (s, size) in enumerate(zip(results_s, sizes)):
        last = i == len(results) - 1
        for k in ("book_title", "subtitle"):
            merged[k] = merged[k] or s[k]
        merged["journey_intro"] = merged["journey_intro"] if merged["journey_intro"]["title"] else s["journey_intro"]
        if last:  # the farewell belongs to the last part
            merged["closing_title"], merged["closing_line"] = s["closing_title"], s["closing_line"]
            merged["best_for_closing"] = s["best_for_closing"] + offset if s["best_for_closing"] else None
        if merged["best_for_cover"] is None and s["best_for_cover"]:
            merged["best_for_cover"] = s["best_for_cover"] + offset
        g = s["group"]
        if not merged["group"]["title"] and g["title"]:
            merged["group"].update(title=g["title"], story=g["story"], footer=g["footer"])
        merged["group"]["photos"] += [n + offset for n in g["photos"]]
        merged["highlights"] += [h for h in s["highlights"] if h not in merged["highlights"]]
        for sc in s["scenes"]:
            merged["scenes"].append({**sc, "photos": [n + offset for n in sc["photos"]]})
        for num, meta in s["photos"].items():
            merged["photos"][num + offset] = meta
        merged["quotes"] += [{"text": q["text"], "photo": q["photo"] + offset if q["photo"] else None} for q in s["quotes"]]
        merged["route"] += [r for r in s["route"] if r["name"].casefold() not in {x["name"].casefold() for x in merged["route"]}]
        offset += size
    merged["highlights"] = merged["highlights"][:6]
    merged["quotes"], merged["route"] = merged["quotes"][:6], merged["route"][:8]
    merged["facts"] = {"places": list(dict.fromkeys(x for r in results_s for x in r["facts"]["places"]))[:10],
                       "travellers": list(dict.fromkeys(x for r in results_s for x in r["facts"]["travellers"]))[:20],
                       "dates": next((r["facts"]["dates"] for r in results_s if r["facts"]["dates"]), "")}
    merged["group"]["photos"] = merged["group"]["photos"][:12]
    return merged


def run_job(job: AIDraftJob) -> None:
    project = job.project
    job.status = AIDraftJob.Status.RUNNING
    job.save(update_fields=["status"])
    try:
        photos = eligible_photos(project)
        if not photos:
            raise AIProviderError("There are no usable photos to build from.")
        notes = []
        if len(photos) > settings.AI_MAX_PHOTOS:
            notes.append(f"Only the first {settings.AI_MAX_PHOTOS} of {len(photos)} photos were used (cost limit).")
            photos = photos[: settings.AI_MAX_PHOTOS]

        provider = get_provider()
        storage = get_storage()
        job.provider, job.model, job.photos_sent = provider.name, provider.model, len(photos)
        trip = project.trip
        context = {"title": trip.title, "destinations": trip.destinations, "companions": trip.companions,
                   "start_date": str(trip.start_date or ""), "end_date": str(trip.end_date or ""),
                   "description": (trip.description or "")[:500], "special_message": (trip.special_message or "")[:300]}

        variant = project.variant
        aspect = float(variant.trim_width_mm) / float(variant.trim_height_mm)
        scene_pages = max(1, project.page_count - 4)  # intro, group, details and closing take the other pages

        results, sizes, tin, tout = [], [], 0, 0
        chunks = _chunks(photos, settings.AI_CHUNK_SIZE)
        for idx, chunk in enumerate(chunks, 1):
            images = []
            for p in chunk:
                raw = storage.read(p.preview_key or p.original_key)  # the preview already carries the customer's edits
                jpeg, _ = normalise_for_ai(raw, edge=settings.AI_IMAGE_EDGE)
                images.append(ImageInput(jpeg, _hint(p)))
            target = max(1, min(len(chunk), round(scene_pages * len(chunk) / len(photos))))
            part = {**context, "target_scenes": target}
            if len(chunks) > 1:
                part["part"] = f"{idx} of {len(chunks)}"
            res = provider.plan_story(images, part)
            results.append(res.story)
            sizes.append(len(chunk))
            tin, tout = tin + res.tokens_in, tout + res.tokens_out

        story = _merge(results, sizes)
        infos = [PhotoInfo(id=str(p.id), width=p.width, height=p.height, flags=list(p.flags or []),
                           favourite=p.is_favourite, caption=p.caption or "",
                           taken=p.taken_at.date() if p.taken_at else None) for p in photos]
        pages, summary = build_book(infos, story, project.page_count, {
            "title": trip.title, "cover_title": trip.cover_title, "special_message": trip.special_message,
            "dedication": trip.dedication, "destinations": trip.destinations, "companions": trip.companions,
            "start_date": trip.start_date, "end_date": trip.end_date}, aspect)
        summary["warnings"] = notes + summary["warnings"]
        job.pages, job.summary, job.tokens_in, job.tokens_out = pages, summary, tin, tout
        job.status = AIDraftJob.Status.DONE
        audit.log("ai.draft", actor=job.requested_by, obj=project, photos=len(photos), model=job.model,
                  tokens_in=tin, tokens_out=tout)
    except AIProviderError as exc:
        job.status, job.error = AIDraftJob.Status.FAILED, str(exc)[:200]
    except Exception:  # noqa: BLE001 - never show internals; the traceback goes to the log
        log.exception("AI draft %s failed", job.id)
        job.status, job.error = AIDraftJob.Status.FAILED, "Could not build the draft. Please try again."
    job.finished_at = timezone.now()
    job.save()
