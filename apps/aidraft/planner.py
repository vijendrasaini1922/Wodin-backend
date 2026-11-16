"""Deterministic book planner: turns the AI's story into a magazine-style travel book.

The AI only writes words and opinions (scenes, titles, short stories, quality, subject position). THIS module decides
the structure, picks layouts, fills photo slots (re-using photos where a page has more slots than the scene has photos),
fits text into its boxes and enforces the page budget. Print safety therefore never depends on a model.

Book structure (cover = page 0, then inner pages):
    cover -> "The Journey Begins" -> one page per scene (day / moment) -> group memories -> trip details
          -> a note in the customer's own words (only if they wrote one) -> closing page
"""
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from . import cropping, layouts

MAX_USES = 3          # a photo normally appears at most this many times; raised (to MAX_USES_CEIL) only when photos are scarce
MAX_USES_CEIL = 5
SLOTS_MAX = 6         # most photos on one scene page (collage6)
BRAND_LINE = "WoDin · Keep Travelling. Keep Creating Memories."
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
SIZE_CQW = {"xl": 12.0, "lg": 7.0, "quote": 4.6, "md": 3.8, "body": 2.9, "sm": 2.3}  # font size as % of page width (same in the preview)
LINE_H = {"script": 1.1, "subtitle": 1.2, "chip": 1.3, "ribbon": 1.2, "footer": 1.25, "body": 1.35, "quote": 1.3}
CHAR_W = {"script": 0.42, "subtitle": 0.42, "footer": 0.42, "ribbon": 0.45, "chip": 0.5, "body": 0.5, "quote": 0.47}


@dataclass
class PhotoInfo:
    id: str
    width: int | None = None
    height: int | None = None
    flags: list = field(default_factory=list)
    favourite: bool = False
    caption: str = ""            # the customer's own caption (always wins)
    ai_caption: str = ""
    taken: date | None = None
    mood: str = ""
    issue: str = "none"          # AI opinion: none / blurry / dark / overexposed / duplicate_of_other
    focus: tuple = (0.5, 0.5)    # where the main subject is (0..1), used as the crop centre
    subject: tuple | None = None  # box (x0, y0, x1, y1) around the main subject, heads included; keeps faces in the crop

    @property
    def ratio(self) -> float:
        return (self.width / self.height) if self.width and self.height else 1.5

    @property
    def weak(self) -> bool:
        return "blurry" in self.flags or "low_resolution" in self.flags or self.issue in ("blurry", "dark", "overexposed")

    @property
    def score(self) -> float:
        return (3.0 if self.favourite else 0.0) - (3.0 if self.weak else 0.0) - (2.0 if self.issue == "duplicate_of_other" else 0.0)


# ---------------------------------------------------------------------------------------------------------------
# sanitising the model's JSON (never trust it)
# ---------------------------------------------------------------------------------------------------------------
def clean(text, limit: int) -> str:
    return _CTRL.sub("", str(text if text is not None else "")).strip()[:limit]


def _int(v, lo, hi):
    try:
        v = int(v)
    except (TypeError, ValueError):
        return None
    return v if lo <= v <= hi else None


def _unit(v, default=0.5):
    try:
        return min(1.0, max(0.0, float(v)))
    except (TypeError, ValueError):
        return default


def _subject_box(p: dict):
    try:
        x0, y0, x1, y1 = (float(p[k]) for k in ("subject_x0", "subject_y0", "subject_x1", "subject_y1"))
    except (KeyError, TypeError, ValueError):
        return None
    x0, x1, y0, y1 = _unit(min(x0, x1)), _unit(max(x0, x1)), _unit(min(y0, y1)), _unit(max(y0, y1))
    if x1 - x0 < 0.05 or y1 - y0 < 0.05 or (x1 - x0 > 0.97 and y1 - y0 > 0.97):  # unusable, or no information (the whole frame)
        return None
    return (round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3))


def _coord(v, limit):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, 3) if -limit <= f <= limit else None


def sanitise_story(raw: dict, n: int) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    meta = {}
    for p in raw.get("photos") or []:
        if not isinstance(p, dict):
            continue
        num = _int(p.get("number"), 1, n)
        if num and num not in meta:
            issue = clean(p.get("quality_issue"), 30)
            meta[num] = {"caption": clean(p.get("caption"), 120), "mood": clean(p.get("mood"), 30),
                         "quality_issue": issue if issue in ("none", "blurry", "dark", "overexposed", "duplicate_of_other") else "none",
                         "focus": (_unit(p.get("focus_x")), _unit(p.get("focus_y"))), "subject": _subject_box(p)}

    seen, scenes = set(), []
    for sc in raw.get("scenes") or []:
        if not isinstance(sc, dict):
            continue
        nums = []
        for v in sc.get("photos") or []:
            num = _int(v, 1, n)
            if num and num not in seen:
                seen.add(num)
                nums.append(num)
        if nums:
            hero = _int(sc.get("hero"), 1, n)
            if hero in nums:
                nums.remove(hero)
                nums.insert(0, hero)
            scenes.append({"title": clean(sc.get("title"), 60), "label": clean(sc.get("label"), 24),
                           "story": clean(sc.get("story"), 400), "footer": clean(sc.get("footer"), 80), "photos": nums})
    missing = [i for i in range(1, n + 1) if i not in seen]  # the model forgot them: keep them, in order, in the nearest scene
    if missing:
        if scenes:
            scenes[-1]["photos"] += missing
        else:
            scenes.append({"title": "", "label": "", "story": "", "footer": "", "photos": missing})

    group_raw = raw.get("group") if isinstance(raw.get("group"), dict) else {}
    group_nums = [x for x in dict.fromkeys(_int(v, 1, n) for v in (group_raw.get("photos") or [])) if x]
    facts_raw = raw.get("facts") if isinstance(raw.get("facts"), dict) else {}
    def fact_list(key, limit):
        vals = facts_raw.get(key)
        return [x for x in (clean(v, 40) for v in (vals if isinstance(vals, list) else [])[:limit]) if x]

    facts = {"places": fact_list("places", 10),
             "travellers": fact_list("travellers", 20),
             "dates": clean(facts_raw.get("dates"), 60)}
    quotes, seen_q = [], set()
    for q in (raw.get("quotes") if isinstance(raw.get("quotes"), list) else [])[:12]:
        if not isinstance(q, dict):
            continue
        text = clean(q.get("text"), 110).strip("\"'\u201c\u201d ").strip()
        if len(text) >= 8 and text.casefold() not in seen_q:
            seen_q.add(text.casefold())
            quotes.append({"text": text, "photo": _int(q.get("photo"), 1, n)})
    route, seen_r = [], set()
    for r in (raw.get("route") if isinstance(raw.get("route"), list) else [])[:12]:
        if not isinstance(r, dict):
            continue
        name, lat, lon = clean(r.get("name"), 40), _coord(r.get("lat"), 90), _coord(r.get("lon"), 180)
        if name and lat is not None and lon is not None and name.casefold() not in seen_r:
            seen_r.add(name.casefold())
            route.append({"name": name, "lat": lat, "lon": lon})
    intro = raw.get("journey_intro") if isinstance(raw.get("journey_intro"), dict) else {}
    return {
        "book_title": clean(raw.get("book_title"), 80), "subtitle": clean(raw.get("subtitle"), 40),
        "closing_title": clean(raw.get("closing_title"), 60), "closing_line": clean(raw.get("closing_line"), 200),
        "best_for_cover": _int(raw.get("best_for_cover"), 1, n), "best_for_closing": _int(raw.get("best_for_closing"), 1, n),
        "journey_intro": {k: clean(intro.get(k), 300 if k == "story" else 80) for k in ("title", "story", "footer")},
        "group": {"title": clean(group_raw.get("title"), 60), "story": clean(group_raw.get("story"), 300),
                  "footer": clean(group_raw.get("footer"), 80), "photos": group_nums},
        "highlights": [h for h in (clean(x, 40) for x in (raw.get("highlights") or [])) if h][:8],
        "scenes": scenes, "photos": meta, "facts": facts, "quotes": quotes[:6], "route": route[:8],
    }


# ---------------------------------------------------------------------------------------------------------------
# text fitting: every string must fit its box. Titles shrink a little, then truncate; paragraphs cut at a sentence.
# ---------------------------------------------------------------------------------------------------------------
def _capacity(slot: dict, aspect: float, scale: float = 1.0) -> tuple[int, int]:
    st = slot["style"]
    variant, fs = st.get("variant", "body"), SIZE_CQW[st.get("size", "body")] * scale
    per_line = max(1, int(slot["w"] * 100 / (fs * CHAR_W.get(variant, 0.5))))
    lines = max(1, int((slot["h"] * 100 / aspect) / (fs * LINE_H.get(variant, 1.3))))
    return per_line, lines


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    sentence = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if sentence >= limit * 0.5:
        return cut[: sentence + 1]
    return cut.rsplit(" ", 1)[0].rstrip(",;: ") + "…"


def fit_text(text: str, slot: dict, aspect: float) -> tuple[str, float]:
    st = slot["style"]
    variant = st.get("variant", "body")
    text = text.strip()
    if variant == "map":  # "Name|lat|lon" rows: never cut a row in half
        return "\n".join([r for r in text.split("\n") if r.strip()][:8]), 1.0
    if variant in ("facts", "note", "route"):
        per_line, lines = _capacity(slot, aspect)
        rows = [r for r in text.split("\n") if r.strip()]
        keep = max(1, lines // (2 if variant == "route" else 1))
        return "\n".join(r[: per_line + 12] for r in rows[:keep]), 1.0
    text = " ".join(text.split())
    per_line, lines = _capacity(slot, aspect)
    if variant == "body":
        return _truncate(text, per_line * lines), 1.0
    if variant == "quote":  # the big opening mark takes about two lines
        return _truncate(text, per_line * max(1, lines - 2)), 1.0
    # one-line items: shrink up to 38% before truncating
    cap1 = per_line * (lines if variant in ("script", "subtitle") else 1)
    if len(text) <= cap1:
        return text, 1.0
    scale = max(0.62, cap1 / len(text))
    per_line2, lines2 = _capacity(slot, aspect, scale)
    cap2 = per_line2 * (lines2 if variant in ("script", "subtitle") else 1)
    return _truncate(text, cap2), round(scale, 2)


def fmt_dates(start, end) -> str:
    if not start:
        return ""
    if not end or end == start:
        return f"{start.day} {start:%B %Y}"
    if (start.year, start.month) == (end.year, end.month):
        return f"{start.day} – {end.day} {end:%B %Y}"
    if start.year == end.year:
        return f"{start.day} {start:%B} – {end.day} {end:%B %Y}"
    return f"{start.day} {start:%B %Y} – {end.day} {end:%B %Y}"


# ---------------------------------------------------------------------------------------------------------------
# page assembly
# ---------------------------------------------------------------------------------------------------------------
class Book:
    def __init__(self, photos: list[PhotoInfo], story: dict, page_count: int, trip: dict, aspect: float):
        self.photos, self.story, self.page_count, self.trip, self.aspect = photos, story, page_count, trip, aspect
        self.by_num = {i + 1: p for i, p in enumerate(photos)}
        for num, m in story["photos"].items():
            p = self.by_num[num]
            p.mood, p.issue, p.focus, p.ai_caption = m["mood"], m["quality_issue"], m["focus"], m["caption"]
            p.subject = m.get("subject")
        self.uses: Counter = Counter()
        self.max_uses = MAX_USES
        self.prev_ids: set = set()
        self.warnings: list = []
        self.polaroid_captions = False   # premium books write the photo's caption on the white edge of a polaroid

    # ---- photo choice ----
    def _mismatch(self, p: PhotoInfo, slot: dict) -> float:
        return abs(math.log(p.ratio / layouts.slot_ratio(slot, self.aspect)))

    def _value(self, p, slot, tier, primary_bonus=0.0) -> float:
        v = p.score * 0.6 - self._mismatch(p, slot) * 2.0 - (0.0, 1.0, 2.5)[tier] - 1.6 * self.uses[p.id] + primary_bonus
        v -= 2.5 * cropping.loss(p.ratio, layouts.slot_ratio(slot, self.aspect), p.focus, p.subject)  # do not crop the faces off
        if self.uses[p.id] == 0:
            v += 2.5  # a photo that has not appeared yet beats re-using one that has
        if p.id in self.prev_ids:
            v -= 2.0  # avoid the same picture on neighbouring pages
        return v

    def fill(self, photo_slots: list[dict], tiers: list[list[PhotoInfo]], hero: PhotoInfo | None = None,
             required: list[PhotoInfo] | None = None, extra_uses: int = 0) -> list[PhotoInfo | None]:
        """Assign one photo per slot, biggest slot first: this scene's photos, then neighbours', then anything (re-use)."""
        order = sorted(range(len(photo_slots)), key=lambda i: -(photo_slots[i]["w"] * photo_slots[i]["h"]))
        result: list[PhotoInfo | None] = [None] * len(photo_slots)
        on_page: set = set()
        pending = {p.id for p in (required or [])}
        for idx in order:
            slot, best, best_v = photo_slots[idx], None, -1e9
            for tier, pool in enumerate(tiers):
                for p in pool:
                    if p.id in on_page or self.uses[p.id] >= self.max_uses + extra_uses:
                        continue
                    if tier > 0 and p.weak:
                        continue
                    bonus = (3.0 if hero is not None and p.id == hero.id and idx == order[0] else 0.0) + (4.0 if p.id in pending else 0.0)
                    v = self._value(p, slot, min(tier, 2), bonus)
                    if v > best_v:
                        best, best_v = p, v
            if best is not None:
                result[idx] = best
                on_page.add(best.id)
                pending.discard(best.id)
        for p in result:
            if p is not None:
                self.uses[p.id] += 1
        self.prev_ids = {p.id for p in result if p is not None}
        return result

    # ---- page builder ----
    def focus_for(self, p: PhotoInfo, slot: dict) -> tuple[float, float]:
        """Where to centre the crop. With a subject box the crop keeps the whole subject (heads first); otherwise the AI's focus point."""
        if p.subject is not None:
            sx0, sy0, sx1, sy1 = p.subject
            fx = (sx0 + sx1) / 2
            fy = sy0 + (sy1 - sy0) * 0.25
            return min(1.0, max(0.0, round(fx, 3))), min(1.0, max(0.0, round(fy, 3)))
        fx = p.focus[0] if p.focus else 0.5
        fy = p.focus[1] if p.focus else 0.20
        return min(1.0, max(0.0, round(fx, 3))), min(1.0, max(0.0, round(fy, 3)))

    def page(self, number: int, key: str, assigned: list, texts: dict) -> dict:
        spec = layouts.get(key, self.aspect)
        els = []
        for i, (slot, p) in enumerate(zip(spec["photos"], assigned)):
            if p is not None:
                fx, fy = self.focus_for(p, slot)
                caption = (p.caption or p.ai_caption)[:60] if self.polaroid_captions and slot["style"].get("frame") == "polaroid" else ""
                els.append({"kind": "photo", "photo": p.id, "text": caption, "x": slot["x"], "y": slot["y"], "w": slot["w"], "h": slot["h"],
                            "z": 0, "style": {**slot["style"], "slot": i, "focus": [round(fx, 3), round(fy, 3)]}})
        for j, slot in enumerate(spec["texts"]):
            raw = texts.get(slot["role"])
            if not raw or not str(raw).strip():
                continue
            text, scale = fit_text(str(raw), slot, self.aspect)
            if text:
                els.append({"kind": "text", "photo": None, "text": text, "x": slot["x"], "y": slot["y"], "w": slot["w"], "h": slot["h"],
                            "z": 10 + j, "style": {**slot["style"], "role": slot["role"], "scale": scale}})
        page = {"number": number, "layout": key, "elements": els}
        if spec.get("paper"):
            page["paper"] = spec["paper"]
        return page


SCENE_TEMPLATES = {
    1: ["day_e", "day_b"], 2: ["day_c", "day_a", "day_b"], 3: ["day_d", "day_a", "day_b"],
    4: ["collage4"], 5: ["collage6"], 6: ["collage6"],
}
BORDERLESS_TEMPLATES = {
    1: ["full_story", "full_story_top"], 2: ["bleed_pair", "bleed_pair_side"], 3: ["bleed_trio", "day_b"],
    4: ["bleed_quad"], 5: ["bleed_six"], 6: ["bleed_six"],
}
PREMIUM_TEMPLATES = {
    1: ["full_story", "full_story_top"], 2: ["inset_hero", "bleed_pair", "bleed_pair_side"], 3: ["scrapbook", "bleed_trio"],
    4: ["mosaic_a", "bleed_quad"], 5: ["mosaic_b", "bleed_six"], 6: ["bleed_six"],
}
MAX_EXTRA_PAGES = 3   # a scene may get at most this many extra pages when the book has room to spare


def _merge_smallest(scenes: list[dict]) -> None:
    i = min(range(len(scenes) - 1), key=lambda k: len(scenes[k]["photos"]) + len(scenes[k + 1]["photos"]))
    a, b = scenes[i], scenes.pop(i + 1)
    a["photos"] += b["photos"]
    a["title"] = a["title"] or b["title"]
    a["story"] = a["story"] or b["story"]


def _split(items: list, parts: int) -> list[list]:
    """Contiguous chunks whose sizes differ by at most one; every chunk is non-empty when len(items) >= parts."""
    base, extra = divmod(len(items), parts)
    out, i = [], 0
    for k in range(parts):
        n = base + (1 if k < extra else 0)
        out.append(items[i:i + n])
        i += n
    return out


def build_book(photos: list[PhotoInfo], story: dict, page_count: int, trip: dict, aspect: float,
               borderless: bool = False, premium: bool = False) -> tuple[list[dict], dict]:
    """Returns (pages, summary). `story` must come from sanitise_story. Page 0 is the cover.
    borderless=True uses the full-bleed layout family (AI Book mode): photos run to the page edge.
    premium=True (implies borderless) adds the art-directed extras: mosaics of large and small photos, taped polaroids with
    handwritten captions, pull-quote pages and an illustrated route map."""
    borderless = borderless or premium
    bk = Book(photos, story, page_count, trip, aspect)
    bk.polaroid_captions = premium
    scenes = [dict(s, photos=list(s["photos"])) for s in story["scenes"]]
    start, end = trip.get("start_date"), trip.get("end_date")
    date_line = clean(trip.get("dates_text"), 60) or fmt_dates(start, end)
    destinations = [clean(d, 40) for d in (trip.get("destinations") or []) if clean(d, 40)]
    companions = [clean(c, 40) for c in (trip.get("companions") or []) if clean(c, 40)]
    message = "\n\n".join(t for t in (clean(trip.get("special_message"), 500), clean(trip.get("dedication"), 300)) if t)
    group_pool = [bk.by_num[n] for n in story["group"]["photos"] if not bk.by_num[n].weak]
    rest = [p for p in photos if not p.weak] or photos
    dropped: list[dict] = []

    has_facts = bool(destinations or companions or date_line or story["highlights"])
    route = [r for r in (story.get("route") or []) if isinstance(r, dict)] if premium else []
    quotes = (story.get("quotes") or []) if premium else []
    n_quotes = min(len(quotes), page_count // 8) if page_count >= 8 else 0
    special = [k for k, ok in (("intro", page_count >= 8), ("group", bool(group_pool) and page_count >= 12),
                               ("map", len(route) >= 2 and page_count >= 10),
                               ("details", page_count >= 10 and (has_facts or not borderless)), ("message", bool(message) and page_count >= 6),
                               ("closing", page_count >= 4)) if ok]
    rest_n = max(1, len(rest))
    scene_pages = max(1, min(page_count - len(special) - n_quotes, math.ceil(rest_n * 1.25)))  # never stretch few photos over empty pages
    while len(scenes) > scene_pages:
        _merge_smallest(scenes)

    # pages per scene: enough to hold its photos, then spare pages go to the busiest scenes
    parts = [max(1, math.ceil(len(s["photos"]) / SLOTS_MAX)) for s in scenes]
    while sum(parts) > scene_pages:
        i = max((k for k in range(len(scenes)) if parts[k] > 1), key=lambda k: parts[k])
        weakest = min(scenes[i]["photos"], key=lambda n: bk.by_num[n].score)
        scenes[i]["photos"].remove(weakest)
        dropped.append({"id": bk.by_num[weakest].id, "reason": "book is full"})
        parts[i] = max(1, math.ceil(len(scenes[i]["photos"]) / SLOTS_MAX))
    for _ in range(scene_pages - sum(parts)):
        open_ = [k for k in range(len(scenes)) if parts[k] < 1 + MAX_EXTRA_PAGES]
        if not open_:
            break
        parts[max(open_, key=lambda k: (len(scenes[k]["photos"]) / parts[k], -k))] += 1

    bk.max_uses = min(MAX_USES_CEIL, max(MAX_USES, math.ceil(3 * scene_pages / rest_n)))

    dated = sorted({p.taken for p in photos if p.taken})
    first_day = dated[0] if len(dated) >= 2 else None

    def members(sc):
        return [bk.by_num[n] for n in sc["photos"]]

    inner: list[dict] = []
    recent: list[str] = []
    for si, sc in enumerate(scenes):
        own = members(sc)
        near = (members(scenes[si - 1]) if si else []) + (members(scenes[si + 1]) if si + 1 < len(scenes) else [])
        far = [p for p in photos if p not in own and p not in near]
        n_parts = parts[si]
        chunks = _split(own, n_parts) if n_parts > 1 and len(own) >= 2 * n_parts else [own] * n_parts
        first_dated = next((p.taken for p in own if p.taken), None)
        day = f"Day {(first_dated - first_day).days + 1}" if first_day and first_dated else ""
        for part, chunk in enumerate(chunks):
            options = (PREMIUM_TEMPLATES if premium else BORDERLESS_TEMPLATES if borderless else SCENE_TEMPLATES)[max(1, min(6, len(chunk)))]
            key = next((o for o in options if o not in recent[-2:]), options[0])
            recent.append(key)
            spec = layouts.get(key, aspect)
            assigned = bk.fill(spec["photos"], [chunk, near, far], hero=chunk[0], required=list(chunk) if part == 0 else [])
            lead = next((p for p in assigned if p is not None), chunk[0])
            mine_caption = next((p.caption for p in chunk if p.caption), "")
            if part == 0:
                texts = {"ribbon": day or sc["label"], "title": sc["title"] or sc["label"] or "A moment to remember",
                         "body": sc["story"], "footer": mine_caption or sc["footer"]}
            else:  # continuation page: lighter on words, still has a heading
                texts = {"ribbon": day or sc["label"], "title": mine_caption or lead.ai_caption or sc["title"], "body": "",
                         "footer": sc["footer"] if part == n_parts - 1 else ""}
            inner.append(bk.page(0, key, assigned, texts))

    def special_page(key, tiers, texts, hero=None, required=None):
        return bk.page(0, key, bk.fill(layouts.get(key, aspect)["photos"], tiers, hero=hero, required=required, extra_uses=3 if key == "closing" else 1), texts)

    front, back = [], []
    hero_key = "bleed_trio" if borderless else "day_a"
    ji = story["journey_intro"]
    if "intro" in special:
        early = [p for sc in scenes[:2] for p in members(sc)]
        front.append(special_page("inset_hero" if premium else hero_key, [early, rest], {
            "title": ji["title"] or "The Journey Begins…",
            "body": ji["story"] or "A new adventure, a new destination, and the first steps of a trip worth remembering.",
            "footer": ji["footer"] or "Let the memories begin…"}))
    if "group" in special:
        g = story["group"]
        back.append(special_page("scrapbook" if premium else hero_key, [group_pool, rest], {
            "title": g["title"] or "Our Group Memories", "body": g["story"] or "Good company. Great times. Forever memories.",
            "footer": g["footer"] or "Together is our favourite place"}, hero=group_pool[0], required=[group_pool[0]]))
    if "map" in special:
        rows = "\n".join(f"{r['name']}|{r['lat']}|{r['lon']}" for r in route[:8])
        back.append(special_page("route_map", [rest, photos], {"title": "Our Route", "map": rows}))
    if "details" in special:
        facts = [("Dates", date_line), ("Place", ", ".join(destinations)), ("Travellers", ", ".join(companions))]
        hl = story["highlights"]
        back.append(special_page("details_b" if borderless else "details", [rest, photos], {
            "title": "Trip Details", "facts": "\n".join(f"{k}|{v}" for k, v in facts if v),
            "note": "\n".join(["Highlights"] + hl) if hl else "", "route": "\n".join(destinations) if len(destinations) >= 2 else ""}))
    if "message" in special:
        back.append(special_page("message_b" if borderless else "message", [rest, photos], {"title": "A Note From the Heart", "body": message}))
    closing_page = None
    if "closing" in special:
        pick = bk.by_num.get(story["best_for_closing"])
        pick = pick if pick and not pick.weak else None
        order = ([pick] if pick else []) + [p for p in reversed(rest) if p is not pick]
        closing_page = special_page("closing", [order, photos], {
            "title": story["closing_title"] or "Until Our Next Adventure…", "body": story["closing_line"], "footer": BRAND_LINE}, hero=pick)

    cover_pick = bk.by_num.get(story["best_for_cover"])
    cover_pick = cover_pick if cover_pick and not cover_pick.weak else None
    order = ([cover_pick] if cover_pick else []) + [p for p in rest if p is not cover_pick]
    cover_title = clean(trip.get("cover_title"), 80) or story["book_title"] or clean(trip.get("title"), 80) or "Our Trip"
    cover = bk.page(0, "cover", bk.fill(layouts.get("cover", aspect)["photos"], [order, photos], hero=cover_pick, extra_uses=2),
                    {"title": cover_title, "subtitle": story["subtitle"] or "Travel Memories",
                     "chip": date_line or ", ".join(destinations)})

    quote_used = 0
    if n_quotes:
        for k in reversed(range(n_quotes)):  # evenly spaced between the scene pages; from the end so earlier positions stay valid
            q = quotes[k]
            anchor = bk.by_num.get(q.get("photo"))
            anchor = anchor if anchor and not anchor.weak else None
            key = "quote_veil" if k % 2 == 0 else "quote_panel"
            tiers = ([[anchor]] if anchor else []) + [rest, photos]
            inner.insert(round((k + 1) * len(inner) / (n_quotes + 1)), special_page(key, tiers, {"quote": q["text"]}, hero=anchor))
            quote_used += 1

    body = front + inner + back + ([closing_page] if closing_page else [])
    for i, pg in enumerate(body, 1):
        pg["number"] = i
    pages = [cover] + body

    placed = {e["photo"] for pg in pages for e in pg["elements"] if e["kind"] == "photo"}
    known = {d["id"] for d in dropped}
    dropped += [{"id": p.id, "reason": "did not fit"} for p in photos if p.id not in placed and p.id not in known]
    reused = sum(1 for c in bk.uses.values() if c > 1)
    warnings = []
    if len(body) < page_count:
        warnings.append(
            f"The book has {len(body)} pages instead of the {page_count} you asked for. Add more photos for a longer book."
            if borderless else f"The draft fills {len(body)} of {page_count} pages. Add photos or pages in the editor, or choose a shorter book.")
    if dropped:
        warnings.append(
            f"{len(dropped)} photo(s) did not fit and were left out. Ask for more pages to include them."
            if borderless else f"{len(dropped)} photo(s) did not fit and were left out. They are still in the photo library.")
    if reused:
        warnings.append(
            f"{reused} photo(s) appear more than once so every page has enough pictures. Add more photos to reduce this."
            if borderless else f"{reused} photo(s) appear more than once so every page has enough pictures. Swap any you prefer.")
    if any(p.weak for p in photos if p.id in placed):
        warnings.append(
            "Some photos may print soft (blurry or low resolution). Look at them closely in the viewer."
            if borderless else "Some placed photos may print poorly (blurry or low resolution). Check them in the preview.")
    return pages, {
        "book_title": cover_title, "pages_used": len(body), "photos_used": len(placed), "photos_total": len(photos),
        "photos_reused": reused, "chapters": [s["title"] for s in scenes if s["title"]], "dropped": dropped, "warnings": warnings,
        "premium": premium, "quotes_used": quote_used, "map_stops": len(route[:8]) if "map" in special else 0,
    }
