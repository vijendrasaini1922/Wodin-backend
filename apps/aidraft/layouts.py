"""Page layout library: the single source of truth for page geometry, used by the AI planner AND served to the editor.

Everything is a fraction (0..1) of the page trim box, so it is independent of paper size. Layouts adapt to the book's
shape: `aspect` = trim width / trim height (A4 portrait ~0.71, square 1.0, A4 landscape ~1.41). Portrait and square books
stack content vertically; landscape books put text on the left and photos on the right.

Photo slot style : frame ("none" | "white" | "polaroid"), rotate (degrees)
Text slot style  : variant, size (xl|lg|md|body|sm), align, onPhoto, scrim ("top"|"bottom"), rotate
Text roles       : the planner fills slots by role (ribbon, title, body, footer, ...). Order in the list = slot index.
"""

LANDSCAPE_FROM = 1.15


def _p(x, y, w, h, **style):
    return {"x": x, "y": y, "w": w, "h": h, "style": style}


def _t(role, label, x, y, w, h, **style):
    return {"role": role, "label": label, "x": x, "y": y, "w": w, "h": h, "style": style}


# ---------- text blocks shared by the "day" family ----------
def _day_text(land, narrow=False):
    if not land:
        return [
            _t("ribbon", "Day label", 0.07, 0.045, 0.34, 0.04, variant="ribbon", size="sm"),
            _t("title", "Title", 0.07, 0.095, 0.86, 0.085, variant="script", size="lg"),
            _t("body", "Story", 0.07, 0.185, 0.86, 0.095, variant="body", size="body"),
            _t("footer", "Footer line", 0.07, 0.925, 0.86, 0.04, variant="footer", size="sm", align="center"),
        ]
    w = 0.27 if narrow else 0.40
    return [
        _t("ribbon", "Day label", 0.05, 0.07, 0.26, 0.06, variant="ribbon", size="sm"),
        _t("title", "Title", 0.05, 0.15, w, 0.17, variant="script", size="lg"),
        _t("body", "Story", 0.05, 0.34, w - 0.01, 0.32, variant="body", size="body"),
        _t("footer", "Footer line", 0.05, 0.88, w, 0.06, variant="footer", size="sm"),
    ]


def day_a(a):
    land = a > LANDSCAPE_FROM
    photos = ([_p(0.48, 0.06, 0.47, 0.58, frame="white"), _p(0.49, 0.68, 0.22, 0.24, frame="polaroid", rotate=-2),
               _p(0.73, 0.68, 0.22, 0.24, frame="polaroid", rotate=2)] if land else
              [_p(0.05, 0.30, 0.90, 0.40, frame="white"), _p(0.07, 0.73, 0.41, 0.17, frame="polaroid", rotate=-2.5),
               _p(0.52, 0.73, 0.41, 0.17, frame="polaroid", rotate=2)])
    return "Day page: hero + 2 polaroids", photos, _day_text(land)


def day_b(a):
    land = a > LANDSCAPE_FROM
    if land:
        photos = [_p(0, 0, 0.54, 1, frame="none"), _p(0.59, 0.65, 0.17, 0.23, frame="polaroid", rotate=-2),
                  _p(0.79, 0.65, 0.17, 0.23, frame="polaroid", rotate=2)]
        texts = [_t("ribbon", "Day label", 0.59, 0.07, 0.26, 0.06, variant="ribbon", size="sm"),
                 _t("title", "Title", 0.59, 0.15, 0.37, 0.17, variant="script", size="lg"),
                 _t("body", "Story", 0.59, 0.34, 0.36, 0.30, variant="body", size="body"),
                 _t("footer", "Footer line", 0.59, 0.93, 0.37, 0.04, variant="footer", size="sm")]
    else:
        photos = [_p(0, 0, 1, 0.60, frame="none"), _p(0.07, 0.73, 0.41, 0.17, frame="polaroid", rotate=-2.5),
                  _p(0.52, 0.73, 0.41, 0.17, frame="polaroid", rotate=2)]
        texts = [_t("ribbon", "Day label", 0.07, 0.05, 0.34, 0.04, variant="ribbon", size="sm", onPhoto=True),
                 _t("title", "Title", 0.07, 0.10, 0.86, 0.085, variant="script", size="lg", onPhoto=True, scrim="top"),
                 _t("body", "Story", 0.07, 0.625, 0.86, 0.095, variant="body", size="body"),
                 _t("footer", "Footer line", 0.07, 0.925, 0.86, 0.04, variant="footer", size="sm", align="center")]
    return "Day page: borderless hero + 2 polaroids", photos, texts


def day_c(a):
    land = a > LANDSCAPE_FROM
    photos = ([_p(0.48, 0.06, 0.47, 0.42, frame="white"), _p(0.48, 0.50, 0.47, 0.42, frame="white")] if land else
              [_p(0.05, 0.30, 0.90, 0.34, frame="white"), _p(0.05, 0.66, 0.90, 0.25, frame="white")])
    return "Day page: 2 photos", photos, _day_text(land)


def day_d(a):
    land = a > LANDSCAPE_FROM
    if land:
        photos = [_p(0.34, 0.06, 0.28, 0.88, frame="white"), _p(0.64, 0.06, 0.32, 0.43, frame="white"),
                  _p(0.64, 0.51, 0.32, 0.43, frame="white")]
    else:
        photos = [_p(0.05, 0.30, 0.45, 0.61, frame="white"), _p(0.52, 0.30, 0.43, 0.295, frame="white"),
                  _p(0.52, 0.615, 0.43, 0.295, frame="white")]
    return "Day page: tall photo + 2", photos, _day_text(land, narrow=True)


def day_e(a):
    land = a > LANDSCAPE_FROM
    photos = ([_p(0.48, 0.06, 0.47, 0.88, frame="white")] if land else [_p(0.05, 0.30, 0.90, 0.58, frame="white")])
    return "Day page: one big photo", photos, _day_text(land)


def collage4(a):
    if a > LANDSCAPE_FROM:
        photos = [_p(0.36, 0.07, 0.29, 0.40, frame="polaroid", rotate=-2), _p(0.67, 0.09, 0.29, 0.40, frame="polaroid", rotate=2),
                  _p(0.36, 0.52, 0.29, 0.40, frame="polaroid", rotate=1.5), _p(0.67, 0.54, 0.29, 0.38, frame="polaroid", rotate=-2)]
        texts = [_t("title", "Title", 0.05, 0.10, 0.28, 0.25, variant="script", size="lg"),
                 _t("body", "Text", 0.05, 0.38, 0.27, 0.30, variant="body", size="body"),
                 _t("footer", "Footer line", 0.05, 0.88, 0.28, 0.06, variant="footer", size="sm")]
    else:
        photos = [_p(0.06, 0.24, 0.42, 0.30, frame="polaroid", rotate=-2), _p(0.52, 0.26, 0.42, 0.30, frame="polaroid", rotate=2),
                  _p(0.06, 0.57, 0.42, 0.30, frame="polaroid", rotate=1.5), _p(0.52, 0.59, 0.42, 0.30, frame="polaroid", rotate=-2.5)]
        texts = [_t("title", "Title", 0.07, 0.05, 0.86, 0.085, variant="script", size="lg"),
                 _t("body", "Text", 0.07, 0.14, 0.86, 0.065, variant="body", size="body"),
                 _t("footer", "Footer line", 0.07, 0.925, 0.86, 0.04, variant="footer", size="sm", align="center")]
    return "Collage: 4 photos", photos, texts


def collage6(a):
    if a > LANDSCAPE_FROM:
        xs, ys = (0.05, 0.36, 0.67), (0.22, 0.57)
        photos = [_p(x, y, 0.28, 0.33, frame="white", rotate=r) for (y, row) in zip(ys, (0, 1))
                  for x, r in zip(xs, ((-1.5, 1, -1) if row == 0 else (1, -1.5, 1.5)))]
        texts = [_t("title", "Title", 0.05, 0.05, 0.9, 0.12, variant="script", size="lg"),
                 _t("footer", "Footer line", 0.05, 0.93, 0.9, 0.05, variant="footer", size="sm", align="center")]
    else:
        ys, xs = (0.17, 0.41, 0.65), (0.06, 0.53)
        rots = [(-1.5, 1.5), (1, -1), (-1, 1.5)]
        photos = [_p(x, y, 0.41, 0.23, frame="white", rotate=rots[i][j]) for i, y in enumerate(ys) for j, x in enumerate(xs)]
        texts = [_t("title", "Title", 0.07, 0.05, 0.86, 0.085, variant="script", size="lg"),
                 _t("footer", "Footer line", 0.07, 0.915, 0.86, 0.05, variant="footer", size="sm", align="center")]
    return "Collage: 6 photos", photos, texts


def details(a):
    if a > LANDSCAPE_FROM:
        photos = [_p(0.67, 0.06, 0.29, 0.88, frame="white")]
        texts = [_t("title", "Title", 0.05, 0.07, 0.40, 0.14, variant="script", size="lg"),
                 _t("facts", "Facts (Label|Value per line)", 0.05, 0.24, 0.38, 0.22, variant="facts", size="body"),
                 _t("note", "Highlights (first line = heading)", 0.05, 0.50, 0.38, 0.36, variant="note", size="body", rotate=-1.5),
                 _t("route", "Route (one stop per line)", 0.46, 0.10, 0.18, 0.80, variant="route", size="body")]
    else:
        photos = [_p(0.05, 0.64, 0.90, 0.30, frame="white")]
        texts = [_t("title", "Title", 0.07, 0.05, 0.86, 0.085, variant="script", size="lg"),
                 _t("facts", "Facts (Label|Value per line)", 0.07, 0.16, 0.52, 0.17, variant="facts", size="body"),
                 _t("note", "Highlights (first line = heading)", 0.07, 0.35, 0.52, 0.25, variant="note", size="body", rotate=-1.5),
                 _t("route", "Route (one stop per line)", 0.66, 0.16, 0.27, 0.44, variant="route", size="body")]
    return "Trip details: facts, highlights, route", photos, texts


def cover(a):
    land = a > LANDSCAPE_FROM
    texts = [
        _t("title", "Title", 0.06 if land else 0.07, 0.08, 0.6 if land else 0.86, 0.26 if land else 0.20,
           variant="script", size="xl", onPhoto=True, scrim="top"),
        _t("subtitle", "Subtitle", 0.06 if land else 0.07, 0.36 if land else 0.30, 0.6 if land else 0.80, 0.07,
           variant="subtitle", size="md", onPhoto=True),
        _t("chip", "Date line", 0.06 if land else 0.07, 0.88, 0.88 if land else 0.86, 0.05,
           variant="chip", size="sm", onPhoto=True, scrim="bottom"),
    ]
    return "Cover: borderless photo + title", [_p(0, 0, 1, 1, frame="none")], texts


def closing(a):
    texts = [_t("title", "Title", 0.08, 0.10, 0.84, 0.18, variant="script", size="xl", align="center", onPhoto=True, scrim="top"),
             _t("body", "Line", 0.08, 0.30, 0.84, 0.07, variant="subtitle", size="md", align="center", onPhoto=True),
             _t("footer", "Brand line", 0.08, 0.90, 0.84, 0.05, variant="chip", size="sm", align="center", onPhoto=True, scrim="bottom")]
    return "Closing: borderless photo + farewell", [_p(0, 0, 1, 1, frame="none")], texts


def message(a):
    if a > LANDSCAPE_FROM:
        photos = [_p(0.05, 0.06, 0.45, 0.88, frame="white")]
        texts = [_t("title", "Title", 0.55, 0.20, 0.40, 0.15, variant="script", size="lg", align="center"),
                 _t("body", "Message", 0.55, 0.38, 0.40, 0.40, variant="body", size="md", align="center")]
    else:
        photos = [_p(0.05, 0.06, 0.90, 0.44, frame="white")]
        texts = [_t("title", "Title", 0.08, 0.54, 0.84, 0.09, variant="script", size="lg", align="center"),
                 _t("body", "Message", 0.10, 0.65, 0.80, 0.25, variant="body", size="md", align="center")]
    return "Message page", photos, texts


# ---------- borderless ("full bleed") family: photos run to the page edge; used by AI Book mode, also selectable in the editor ----------
# Geometry is the same for every book shape (these pages are designed for portrait books). Tiny gutters (0.6%) keep photos apart.
def _bleed_text(width=0.86, x=0.07):
    # Every text on a photo carries its own scrim: renderers draw only the widest one per side, so readability never
    # depends on an optional element (for example a missing "Day N" label) being present.
    return [
        _t("ribbon", "Day label", x, 0.05, 0.34, 0.04, variant="ribbon", size="sm", onPhoto=True, scrim="top"),
        _t("title", "Title", x, 0.10, width, 0.10, variant="script", size="lg", onPhoto=True, scrim="top"),
        _t("body", "Story", x, 0.79, width, 0.10, variant="body", size="body", onPhoto=True, scrim="bottom"),
        _t("footer", "Footer line", x, 0.915, width, 0.04, variant="footer", size="sm", align="center", onPhoto=True, scrim="bottom"),
    ]


def full_story(a):
    texts = [
        _t("ribbon", "Day label", 0.07, 0.58, 0.34, 0.04, variant="ribbon", size="sm", onPhoto=True, scrim="bottom"),
        _t("title", "Title", 0.07, 0.64, 0.86, 0.10, variant="script", size="lg", onPhoto=True, scrim="bottom"),
        _t("body", "Story", 0.07, 0.75, 0.86, 0.10, variant="body", size="body", onPhoto=True, scrim="bottom"),
        _t("footer", "Footer line", 0.07, 0.915, 0.86, 0.04, variant="footer", size="sm", align="center", onPhoto=True, scrim="bottom"),
    ]
    return "Borderless: one photo, story at the bottom", [_p(0, 0, 1, 1, frame="none")], texts


def full_story_top(a):
    texts = [
        _t("ribbon", "Day label", 0.07, 0.05, 0.34, 0.04, variant="ribbon", size="sm", onPhoto=True, scrim="top"),
        _t("title", "Title", 0.07, 0.10, 0.86, 0.10, variant="script", size="lg", onPhoto=True, scrim="top"),
        _t("body", "Story", 0.07, 0.21, 0.86, 0.10, variant="body", size="body", onPhoto=True, scrim="top"),
        _t("footer", "Footer line", 0.07, 0.905, 0.86, 0.04, variant="footer", size="sm", align="center", onPhoto=True, scrim="bottom"),
    ]
    return "Borderless: one photo, story at the top", [_p(0, 0, 1, 1, frame="none")], texts


def bleed_pair(a):
    if a > LANDSCAPE_FROM:  # two wide strips would crop badly: use two halves instead
        photos = [_p(0, 0, 0.497, 1, frame="none"), _p(0.503, 0, 0.497, 1, frame="none")]
    else:
        photos = [_p(0, 0, 1, 0.497, frame="none"), _p(0, 0.503, 1, 0.497, frame="none")]
    return "Borderless: 2 photos stacked", photos, _bleed_text()


def bleed_pair_side(a):
    photos = [_p(0, 0, 0.497, 1, frame="none"), _p(0.503, 0, 0.497, 1, frame="none")]
    return "Borderless: 2 photos side by side", photos, _bleed_text()


def bleed_trio(a):
    photos = [_p(0, 0, 0.597, 1, frame="none"), _p(0.603, 0, 0.397, 0.497, frame="none"), _p(0.603, 0.503, 0.397, 0.497, frame="none")]
    return "Borderless: 3 photos", photos, _bleed_text(width=0.48)


def bleed_quad(a):
    photos = [_p(0, 0, 0.497, 0.497, frame="none"), _p(0.503, 0, 0.497, 0.497, frame="none"),
              _p(0, 0.503, 0.497, 0.497, frame="none"), _p(0.503, 0.503, 0.497, 0.497, frame="none")]
    texts = [_t("title", "Title", 0.07, 0.05, 0.86, 0.10, variant="script", size="lg", onPhoto=True, scrim="top"),
             _t("footer", "Footer line", 0.07, 0.91, 0.86, 0.04, variant="footer", size="sm", align="center", onPhoto=True, scrim="bottom")]
    return "Borderless: 4 photos", photos, texts


def bleed_six(a):
    if a > LANDSCAPE_FROM:
        photos = [_p(x, y, 0.3307, 0.497, frame="none") for y in (0, 0.503) for x in (0, 0.3347, 0.6693)]
    else:
        photos = [_p(x, y, 0.497, 0.329, frame="none") for y in (0, 0.3355, 0.671) for x in (0, 0.503)]
    texts = [_t("title", "Title", 0.07, 0.05, 0.86, 0.10, variant="script", size="lg", onPhoto=True, scrim="top"),
             _t("footer", "Footer line", 0.07, 0.91, 0.86, 0.04, variant="footer", size="sm", align="center", onPhoto=True, scrim="bottom")]
    return "Borderless: 6 photos", photos, texts


def details_b(a):
    if a > LANDSCAPE_FROM:
        photos = [_p(0.62, 0, 0.38, 1, frame="none")]
        texts = [_t("title", "Title", 0.05, 0.05, 0.50, 0.13, variant="script", size="lg"),
                 _t("facts", "Facts (Label|Value per line)", 0.05, 0.19, 0.50, 0.35, variant="facts", size="body"),
                 _t("note", "Highlights (first line = heading)", 0.05, 0.58, 0.30, 0.33, variant="note", size="body", rotate=-1.5),
                 _t("route", "Route (one stop per line)", 0.39, 0.58, 0.20, 0.36, variant="route", size="body")]
        return "Borderless: trip details with photo column", photos, texts
    if a > 0.85:  # square: text is sized by page width, so each box needs more of the page height than on a portrait page
        photos = [_p(0, 0.72, 1, 0.28, frame="none")]
        texts = [_t("title", "Title", 0.07, 0.04, 0.86, 0.11, variant="script", size="lg"),
                 _t("facts", "Facts (Label|Value per line)", 0.07, 0.16, 0.52, 0.26, variant="facts", size="body"),
                 _t("note", "Highlights (first line = heading)", 0.07, 0.44, 0.52, 0.25, variant="note", size="body", rotate=-1.5),
                 _t("route", "Route (one stop per line)", 0.66, 0.16, 0.27, 0.34, variant="route", size="body")]
        return "Borderless: trip details with photo strip", photos, texts
    photos = [_p(0, 0.62, 1, 0.38, frame="none")]
    texts = [_t("title", "Title", 0.07, 0.05, 0.86, 0.085, variant="script", size="lg"),
             _t("facts", "Facts (Label|Value per line)", 0.07, 0.16, 0.52, 0.17, variant="facts", size="body"),
             _t("note", "Highlights (first line = heading)", 0.07, 0.35, 0.52, 0.25, variant="note", size="body", rotate=-1.5),
             _t("route", "Route (one stop per line)", 0.66, 0.16, 0.27, 0.40, variant="route", size="body")]
    return "Borderless: trip details with photo strip", photos, texts


def message_b(a):
    if a > LANDSCAPE_FROM:
        texts = [_t("title", "Title", 0.55, 0.20, 0.40, 0.15, variant="script", size="lg", align="center"),
                 _t("body", "Message", 0.55, 0.38, 0.40, 0.40, variant="body", size="md", align="center")]
        return "Borderless: message page", [_p(0, 0, 0.5, 1, frame="none")], texts
    photos = [_p(0, 0, 1, 0.50, frame="none")]
    texts = [_t("title", "Title", 0.08, 0.55, 0.84, 0.09, variant="script", size="lg", align="center"),
             _t("body", "Message", 0.10, 0.66, 0.80, 0.26, variant="body", size="md", align="center")]
    return "Borderless: message page", photos, texts


# ---------- premium family (AI Book mode only; NOT served to the editor, which has no renderer for quotes, maps or paper) ----------
# Text boxes are sized for portrait pages; `_s(a)` grows their height on squarer pages because text size follows page width.
def _s(a):
    return min(1.45, max(1.0, a / 0.707))


def inset_hero(a):
    s = _s(a)
    if a > LANDSCAPE_FROM:
        photos = [_p(0, 0, 1, 1, frame="none"), _p(0.60, 0.46, 0.31, 0.42, frame="polaroid", rotate=3, tape=True)]
        texts = [_t("ribbon", "Day label", 0.05, 0.07, 0.26, 0.06, variant="ribbon", size="sm", onPhoto=True, scrim="top"),
                 _t("title", "Title", 0.05, 0.15, 0.45, 0.16, variant="script", size="lg", onPhoto=True, scrim="top"),
                 _t("body", "Story", 0.05, 0.62, 0.45, 0.24, variant="body", size="body", onPhoto=True, scrim="bottom"),
                 _t("footer", "Footer line", 0.05, 0.92, 0.45, 0.05, variant="footer", size="sm", onPhoto=True, scrim="bottom")]
    else:
        photos = [_p(0, 0, 1, 1, frame="none"), _p(0.52, 0.55, 0.40, 0.27, frame="polaroid", rotate=3, tape=True)]
        texts = [_t("ribbon", "Day label", 0.07, 0.05, 0.34, 0.04, variant="ribbon", size="sm", onPhoto=True, scrim="top"),
                 _t("title", "Title", 0.07, 0.10, 0.86, 0.10, variant="script", size="lg", onPhoto=True, scrim="top"),
                 _t("body", "Story", 0.07, 0.66, 0.40, min(0.22, 0.17 * s), variant="body", size="body", onPhoto=True, scrim="bottom"),
                 _t("footer", "Footer line", 0.07, 0.915, 0.86, 0.04, variant="footer", size="sm", align="center", onPhoto=True, scrim="bottom")]
    return "Premium: full photo with a taped polaroid", photos, texts


def mosaic_a(a):
    if a > LANDSCAPE_FROM:
        photos = [_p(0, 0, 0.62, 1, frame="none")] + [_p(0.628, y, 0.372, 0.328, frame="none") for y in (0, 0.336, 0.672)]
    else:
        photos = [_p(0, 0, 1, 0.60, frame="none")] + [_p(x, 0.606, 0.3293, 0.394, frame="none") for x in (0, 0.3353, 0.6707)]
    texts = [_t("title", "Title", 0.07, 0.05, 0.50 if a > LANDSCAPE_FROM else 0.86, 0.10, variant="script", size="lg", onPhoto=True, scrim="top"),
             _t("footer", "Footer line", 0.07, 0.93, 0.86, 0.04, variant="footer", size="sm", align="center", onPhoto=True, scrim="bottom")]
    return "Premium mosaic: one large + three small", photos, texts


def mosaic_b(a):
    if a > LANDSCAPE_FROM:
        photos = [_p(0, 0, 0.45, 0.62, frame="none"), _p(0.456, 0, 0.544, 0.62, frame="none")] + \
                 [_p(x, 0.628, 0.3293, 0.372, frame="none") for x in (0, 0.3353, 0.6707)]
    else:
        photos = [_p(0, 0, 0.64, 0.58, frame="none"), _p(0.646, 0, 0.354, 0.58, frame="none")] + \
                 [_p(x, 0.586, 0.3293, 0.414, frame="none") for x in (0, 0.3353, 0.6707)]
    texts = [_t("title", "Title", 0.06, 0.05, 0.50, 0.10, variant="script", size="lg", onPhoto=True, scrim="top"),
             _t("footer", "Footer line", 0.07, 0.93, 0.86, 0.04, variant="footer", size="sm", align="center", onPhoto=True, scrim="bottom")]
    return "Premium mosaic: two large + three small", photos, texts


def scrapbook(a):
    s = _s(a)
    if a > LANDSCAPE_FROM:
        photos = [_p(0.40, 0.08, 0.30, 0.50, frame="polaroid", rotate=-3, tape=True), _p(0.68, 0.14, 0.28, 0.46, frame="polaroid", rotate=3, tape=True),
                  _p(0.46, 0.50, 0.28, 0.44, frame="polaroid", rotate=2, tape=True)]
        texts = [_t("title", "Title", 0.05, 0.10, 0.32, 0.20, variant="script", size="lg"),
                 _t("body", "Story", 0.05, 0.34, 0.31, 0.40, variant="body", size="body"),
                 _t("footer", "Footer line", 0.05, 0.88, 0.31, 0.06, variant="footer", size="sm")]
    else:
        photos = [_p(0.07, 0.16, 0.58, 0.31, frame="polaroid", rotate=-2.5, tape=True), _p(0.52, 0.28, 0.40, 0.28, frame="polaroid", rotate=3, tape=True),
                  _p(0.10, 0.53, 0.42, 0.28, frame="polaroid", rotate=2, tape=True)]
        texts = [_t("title", "Title", 0.07, 0.05, 0.86, 0.09, variant="script", size="lg"),
                 _t("body", "Story", 0.07, 0.83, 0.86, min(0.10, 0.075 * s), variant="body", size="body"),
                 _t("footer", "Footer line", 0.07, 0.945, 0.86, 0.035, variant="footer", size="sm", align="center")]
    return "Premium scrapbook: three taped polaroids on paper", photos, texts


def quote_panel(a):
    if a > LANDSCAPE_FROM:
        photos = [_p(0, 0, 0.55, 1, frame="none")]
        texts = [_t("quote", "Quote", 0.61, 0.22, 0.33, 0.56, variant="quote", size="quote", align="center", tone="light")]
    else:
        photos = [_p(0, 0, 1, 0.56, frame="none")]
        texts = [_t("quote", "Quote", 0.12, 0.62, 0.76, min(0.32, 0.26 * _s(a)), variant="quote", size="quote", align="center", tone="light")]
    return "Premium quote: photo and a coloured panel", photos, texts


def quote_veil(a):
    texts = [_t("quote", "Quote", 0.12, 0.28, 0.76, 0.44, variant="quote", size="quote", align="center", onPhoto=True, veil=True)]
    return "Premium quote: photo under a soft veil", [_p(0, 0, 1, 1, frame="none")], texts


def route_map(a):
    if a > LANDSCAPE_FROM:
        photos = [_p(0.69, 0.10, 0.27, 0.40, frame="polaroid", rotate=-3, tape=True), _p(0.69, 0.54, 0.27, 0.38, frame="polaroid", rotate=3, tape=True)]
        texts = [_t("title", "Title", 0.05, 0.03, 0.55, 0.11, variant="script", size="lg"),
                 _t("map", "Route (Name|lat|lon per line)", 0.04, 0.16, 0.60, 0.80, variant="map", size="body")]
    else:
        photos = [_p(0.08, 0.77, 0.30, 0.19, frame="polaroid", rotate=-3, tape=True), _p(0.62, 0.77, 0.30, 0.19, frame="polaroid", rotate=3, tape=True)]
        texts = [_t("title", "Title", 0.07, 0.04, 0.86, 0.09, variant="script", size="lg"),
                 _t("map", "Route (Name|lat|lon per line)", 0.06, 0.15, 0.88, 0.60, variant="map", size="body")]
    return "Premium: illustrated route map", photos, texts


PREMIUM = {
    "inset_hero": inset_hero, "mosaic_a": mosaic_a, "mosaic_b": mosaic_b, "scrapbook": scrapbook,
    "quote_panel": quote_panel, "quote_veil": quote_veil, "route_map": route_map,
}
# The paper a page is printed on when it is not fully covered by photos (default: plain white paper).
PAPER = {"scrapbook": "cream", "route_map": "parchment", "quote_panel": "forest"}


BUILDERS = {
    "cover": cover, "day_a": day_a, "day_b": day_b, "day_c": day_c, "day_d": day_d, "day_e": day_e, "collage4": collage4,
    "collage6": collage6, "details": details, "closing": closing, "message": message,
    "full_story": full_story, "full_story_top": full_story_top, "bleed_pair": bleed_pair, "bleed_pair_side": bleed_pair_side,
    "bleed_trio": bleed_trio, "bleed_quad": bleed_quad, "bleed_six": bleed_six, "details_b": details_b, "message_b": message_b,
}
DEFAULT_ASPECT = 0.707


def get(key: str, aspect: float = DEFAULT_ASPECT) -> dict:
    label, photos, texts = (BUILDERS.get(key) or PREMIUM[key])(aspect)
    return {"key": key, "label": label, "photos": photos, "texts": texts, "paper": PAPER.get(key, "")}


def all_layouts(aspect: float = DEFAULT_ASPECT) -> list[dict]:
    return [get(k, aspect) for k in BUILDERS]


def slot_ratio(box: dict, aspect: float) -> float:
    """Physical width/height of a photo slot (page shape taken into account)."""
    return (box["w"] * aspect) / box["h"]
