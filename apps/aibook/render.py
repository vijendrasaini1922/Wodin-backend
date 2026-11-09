"""Draws planner pages (fractional element lists) as images, then assembles the PDF.

Mirrors the browser preview (`PagePreview.tsx`): same font sizes (% of page width), same frames, scrims and text variants,
so what the customer sees in the viewer is what is in the PDF. Pure Pillow: no system libraries needed.

Latin script only (English, romanised Hindi, accents). Characters a font cannot draw are replaced, never shown as boxes.
"""
import io
import math
import random
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

FONT_DIR = Path(__file__).resolve().parent / "fonts"
MM_PER_INCH = 25.4

# Must match SIZE_CQW / LINE_H in planner.py and PagePreview.tsx
SIZE_CQW = {"xl": 12.0, "lg": 7.0, "quote": 4.6, "md": 3.8, "body": 2.9, "sm": 2.3}
LINE_H = {"script": 1.1, "subtitle": 1.2, "chip": 1.3, "ribbon": 1.2, "footer": 1.25, "body": 1.35, "quote": 1.3}

FOREST, INK, TERRACOTTA, TERRACOTTA_DEEP = (40, 61, 53), (31, 42, 38), (197, 139, 87), (168, 113, 63)
IVORY = (244, 239, 230)
PAPER, NOTE = (255, 253, 248), (246, 231, 168)
PAPERS = {"": PAPER, "cream": (250, 244, 230), "parchment": (241, 229, 203), "forest": FOREST}
TAPE = (236, 214, 160)


@lru_cache(maxsize=64)
def _font(name: str, px: int, weight: int | None = None, opsz: int | None = None) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(FONT_DIR / f"{name}.ttf"), max(6, px))
    try:
        axes = f.get_variation_axes()
        values = []
        for ax in axes:
            n = ax["name"]
            n = n.decode() if isinstance(n, bytes) else n
            if n == "Weight":
                values.append(min(ax["maximum"], max(ax["minimum"], weight or ax["default"])))
            else:  # optical size etc.
                values.append(min(ax["maximum"], max(ax["minimum"], opsz or ax["default"])))
        f.set_variation_by_axes(values)
    except (OSError, AttributeError):
        pass
    return f


def _supports(font: ImageFont.FreeTypeFont, ch: str) -> bool:
    if ch in " \n\t":
        return True
    try:
        return bytes(font.getmask(ch)) != bytes(font.getmask("\uffff")) or font.getmask(ch).size != font.getmask("\uffff").size
    except Exception:  # noqa: BLE001
        return False


def clean_for(font: ImageFont.FreeTypeFont, text: str) -> str:
    """Replace what the font cannot draw (Devanagari, emoji) with '?' so no tofu boxes reach a printed page."""
    return "".join(c if _supports(font, c) else "?" for c in text)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        words, cur = para.split(), ""
        for w in words:
            trial = f"{cur} {w}".strip()
            if draw.textlength(trial, font=font) <= width or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
    return lines


def _cover(img: Image.Image, w: int, h: int, focus: tuple[float, float]) -> Image.Image:
    """Scale to fill w x h, cropping around the focus point (like CSS object-fit: cover + object-position)."""
    iw, ih = img.size
    scale = max(w / iw, h / ih)
    nw, nh = max(w, math.ceil(iw * scale)), max(h, math.ceil(ih * scale))
    img = img.resize((nw, nh), Image.Resampling.LANCZOS)
    left = int(round((nw - w) * min(1, max(0, focus[0]))))
    top = int(round((nh - h) * min(1, max(0, focus[1]))))
    return img.crop((left, top, left + w, top + h))


class PageRenderer:
    """Renders pages at `width_px` x `height_px` of the TRIM box, plus an optional bleed on every side."""

    def __init__(self, width_px: int, height_px: int, bleed_px: int = 0, load_photo=None):
        self.w, self.h, self.bleed = width_px, height_px, bleed_px
        self.load_photo = load_photo  # (photo_id) -> PIL.Image | None

    # ----- geometry -----
    def _box(self, e) -> tuple[int, int, int, int]:
        x0, y0 = round(e["x"] * self.w), round(e["y"] * self.h)
        x1, y1 = round((e["x"] + e["w"]) * self.w), round((e["y"] + e["h"]) * self.h)
        return x0, y0, max(x1 - x0, 1), max(y1 - y0, 1)

    def _ext(self, e, x0, y0, bw, bh):
        """Full-bleed photos grow into the bleed area on the sides where they touch the trim edge."""
        l = self.bleed if e["x"] <= 0.001 else 0
        t = self.bleed if e["y"] <= 0.001 else 0
        r = self.bleed if e["x"] + e["w"] >= 0.999 else 0
        b = self.bleed if e["y"] + e["h"] >= 0.999 else 0
        return x0 - l, y0 - t, bw + l + r, bh + t + b

    # ----- drawing -----
    def render(self, page: dict) -> Image.Image:
        B = self.bleed
        canvas = Image.new("RGB", (self.w + 2 * B, self.h + 2 * B), PAPERS.get(page.get("paper") or "", PAPER))
        els = sorted(page["elements"], key=lambda e: e["z"])
        for e in els:
            if e["kind"] == "photo":
                self._photo(canvas, e)
        if any(e["kind"] == "text" and e["text"].strip() and (e.get("style") or {}).get("veil") for e in els):
            canvas.paste(Image.composite(Image.new("RGB", canvas.size, (12, 18, 16)), canvas, Image.new("L", canvas.size, 112)))  # soft veil for a quote
        for side, e in self._scrims(els):
            self._scrim(canvas, e, side)
        for e in els:
            if e["kind"] == "text" and e["text"].strip():
                self._text(canvas, e)
        return canvas

    def _photo(self, canvas: Image.Image, e: dict) -> None:
        st = e.get("style") or {}
        photo = self.load_photo(e["photo"]) if self.load_photo else None
        x0, y0, bw, bh = self._box(e)
        frame = st.get("frame", "none")
        unit = self.w / 100  # 1cqw
        if frame == "none":
            ex, ey, ew, eh = self._ext(e, x0, y0, bw, bh)
            tile = self._fit(photo, ew, eh, st.get("focus"))
            canvas.paste(tile, (ex + self.bleed, ey + self.bleed))
            return
        pad_l = pad_r = pad_t = round(unit * (1.3 if frame == "polaroid" else 1.2))
        pad_b = round(unit * (4.0 if frame == "polaroid" else 1.2))
        card = Image.new("RGBA", (bw, bh), (255, 255, 255, 255))
        iw, ih = max(1, bw - pad_l - pad_r), max(1, bh - pad_t - pad_b)
        card.paste(self._fit(photo, iw, ih, st.get("focus")), (pad_l, pad_t))
        caption = (e.get("text") or "").strip()
        if caption and frame == "polaroid":
            self._caption(card, caption, pad_b, pad_l, unit)
        rot = float(st.get("rotate") or 0)
        pad = round(unit * 2.5)
        layer = Image.new("RGBA", (bw + 2 * pad, bh + 2 * pad), (0, 0, 0, 0))
        shadow = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rectangle((pad, pad + round(unit * 0.4), pad + bw, pad + bh + round(unit * 0.4)), fill=(31, 42, 38, 70))
        layer = Image.alpha_composite(layer, shadow.filter(ImageFilter.GaussianBlur(unit * 0.6)))
        layer.paste(card, (pad, pad))
        if st.get("tape"):
            tw, th = max(8, round(bw * 0.28)), max(6, round(unit * 3.4))
            tape = Image.new("RGBA", (tw, th), TAPE + (178,))
            ImageDraw.Draw(tape).line([(0, 0), (tw, 0)], fill=(255, 255, 255, 70), width=max(1, round(unit * 0.15)))
            tape = tape.rotate(-5 if rot >= 0 else 5, resample=Image.Resampling.BICUBIC, expand=True)
            layer.alpha_composite(tape, (max(0, pad + bw // 2 - tape.width // 2), max(0, pad - round(unit * 1.4))))
        if rot:
            layer = layer.rotate(-rot, resample=Image.Resampling.BICUBIC, expand=False)
        canvas.paste(layer, (x0 + self.bleed - pad, y0 + self.bleed - pad), layer)

    @staticmethod
    def _caption(card: Image.Image, text: str, pad_b: int, pad_x: int, unit: float) -> None:
        """Handwritten caption on the white edge of a polaroid; shrinks to fit one line."""
        d = ImageDraw.Draw(card)
        size = max(8, round(unit * 2.7))
        font = _font("Caveat", size, 600)
        text = clean_for(font, " ".join(text.split()))
        room = card.width - 2 * pad_x - 4
        while size > unit * 1.6 and d.textlength(text, font=font) > room:
            size -= 1
            font = _font("Caveat", size, 600)
        while text and d.textlength(text, font=font) > room:
            text = text[:-2].rstrip() + "\u2026" if len(text) > 2 else ""
        if text:
            x = (card.width - d.textlength(text, font=font)) / 2
            d.text((x, card.height - pad_b + (pad_b - size) / 2 - size * 0.12), text, font=font, fill=INK + (255,))

    def _fit(self, photo, w, h, focus) -> Image.Image:
        if photo is None:
            return Image.new("RGB", (w, h), (235, 228, 214))
        f = tuple(focus) if focus and len(focus) == 2 else (0.5, 0.5)
        return _cover(photo, w, h, (float(f[0]), float(f[1])))

    # ----- scrims: one per side (widest wins), same rule as the browser preview -----
    def _scrims(self, els):
        out = []
        for side in ("top", "bottom"):
            cand = [e for e in els if e["kind"] == "text" and e["text"].strip() and (e.get("style") or {}).get("onPhoto")
                    and (e.get("style") or {}).get("scrim") == side and (e.get("style") or {}).get("size")]
            if cand:
                reach = (lambda e: e["y"] + e["h"]) if side == "top" else (lambda e: 1 - e["y"])
                out.append((side, max(cand, key=reach)))
        return out

    def _scrim(self, canvas: Image.Image, e: dict, side: str) -> None:
        """Soft black fade from the page edge to just past the text (same stops as the preview)."""
        B, fade = self.bleed, 0.12
        if side == "top":
            y0, y1 = 0, round(min(1.0, e["y"] + e["h"] + fade) * self.h) + B
            strength = 0.5
        else:
            y0, y1 = round(max(0.0, e["y"] - fade) * self.h) + B, canvas.height
            strength = 0.55
        height = max(1, y1 - y0)
        ramp = Image.linear_gradient("L").resize((1, height))  # 0 at the top of the band -> 255 at the bottom
        if side == "top":
            ramp = ImageOps.flip(ramp)  # strongest at the very top edge
        alpha = ramp.point(lambda v: int(v * strength)).resize((canvas.width, height))
        region = canvas.crop((0, y0, canvas.width, y0 + height))
        canvas.paste(Image.composite(Image.new("RGB", region.size, (0, 0, 0)), region, alpha), (0, y0))

    # ----- text -----
    def _text(self, canvas: Image.Image, e: dict) -> None:
        st = e.get("style") or {}
        variant, size = st.get("variant", "body"), st.get("size")
        if not size:  # a page saved by the old editor: draw plainly
            variant, size = "body", "body"
        fs = SIZE_CQW.get(size, 2.9) * float(st.get("scale") or 1.0) * self.w / 100
        x0, y0, bw, bh = self._box(e)
        on_photo, centre = bool(st.get("onPhoto")), st.get("align") == "center"
        pad = round(self.w * 0.02)
        layer = Image.new("RGBA", (bw + 2 * pad, bh + 2 * pad), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        ox, oy = pad, pad
        txt = e["text"]
        white = (255, 255, 255)

        def draw_lines(lines, font, colour, lh, shadow=False, width=bw, start_y=0, upper=False, spacing=0.0):
            y = start_y
            for ln in lines:
                if upper:
                    ln = ln.upper()
                lw = d.textlength(ln, font=font) + spacing * max(0, len(ln) - 1)
                x = ox + ((width - lw) / 2 if centre else 0)
                if shadow:
                    for dx, dy, a in ((0, 2, 110), (1, 1, 60)):
                        self._text_at(d, (x + dx, oy + y + dy), ln, font, (0, 0, 0, a), spacing)
                self._text_at(d, (x, oy + y), ln, font, colour + (255,), spacing)
                y += lh
                if y > bh + fs:  # never draw outside the box by more than a line
                    break
            return y

        if variant == "script":
            font = _font("Caveat", round(fs), 700)
            txt = clean_for(font, " ".join(txt.split()))
            draw_lines(_wrap(d, txt, font, bw), font, white if on_photo else FOREST, fs * LINE_H["script"], shadow=on_photo)
        elif variant == "subtitle":
            font = _font("PlayfairDisplay-Italic", round(fs), 400)
            txt = clean_for(font, " ".join(txt.split()))
            draw_lines(_wrap(d, txt, font, bw), font, white if on_photo else FOREST, fs * LINE_H["subtitle"], shadow=on_photo)
        elif variant == "ribbon":
            font = _font("Inter", round(fs), 600, 14)
            label = clean_for(font, txt.strip().upper())
            sp = fs * 0.12
            tw = d.textlength(label, font=font) + sp * max(0, len(label) - 1)
            padx_l, padx_r, pady = self.w * 0.018, self.w * 0.026, self.w * 0.005
            rw, rh = min(bw, tw + padx_l + padx_r), fs * LINE_H["ribbon"] + 2 * pady
            pts = [(ox, oy), (ox + rw, oy), (ox + rw * 0.94, oy + rh / 2), (ox + rw, oy + rh), (ox, oy + rh)]
            d.polygon(pts, fill=TERRACOTTA + (255,))
            self._text_at(d, (ox + padx_l, oy + pady + fs * 0.05), label, font, (255, 255, 255, 255), sp)
        elif variant == "chip":
            font = _font("Inter", round(fs), 500, 14)
            txt = clean_for(font, " ".join(txt.split()).upper())
            draw_lines(_wrap_spaced(d, txt, font, bw, fs * 0.14), font, white if on_photo else FOREST, fs * LINE_H["chip"], shadow=on_photo, spacing=fs * 0.14)
        elif variant == "footer":
            font = _font("PlayfairDisplay-Italic", round(fs), 400)
            txt = clean_for(font, " ".join(txt.split()))
            draw_lines(_wrap(d, txt, font, bw), font, white if on_photo else TERRACOTTA_DEEP, fs * LINE_H["footer"], shadow=on_photo)
        elif variant == "facts":
            lab, val = _font("Inter", round(fs * 0.8), 600, 14), _font("Inter", round(fs), 400, 14)
            y = 0.0
            for row in [r for r in txt.split("\n") if r.strip()]:
                k, _, v = row.partition("|")
                self._text_at(d, (ox, oy + y), clean_for(lab, k.upper()), lab, TERRACOTTA_DEEP + (255,), fs * 0.08)
                y += fs * 0.95
                for ln in _wrap(d, clean_for(val, v), val, bw):
                    self._text_at(d, (ox, oy + y), ln, val, INK + (255,), 0)
                    y += fs * 1.25
                y += fs * 0.45
        elif variant == "note":
            rows = [r for r in txt.split("\n") if r.strip()]
            pad_n = self.w * 0.016
            sh = Image.new("RGBA", layer.size, (0, 0, 0, 0))
            ImageDraw.Draw(sh).rectangle((ox + 2, oy + self.w * 0.004, ox + bw + 2, oy + bh + self.w * 0.004), fill=(31, 42, 38, 60))
            layer = Image.alpha_composite(layer, sh.filter(ImageFilter.GaussianBlur(self.w * 0.004)))
            d = ImageDraw.Draw(layer)
            d.rectangle((ox, oy, ox + bw, oy + bh), fill=NOTE + (255,))
            head_f = _font("Caveat", round(fs * 1.4), 700)
            item_f = _font("Inter", round(fs), 400, 14)
            y = pad_n
            if rows:
                d.text((ox + pad_n, oy + y), clean_for(head_f, rows[0]), font=head_f, fill=INK + (255,))
                y += fs * 1.4 * 1.1
                for it in rows[1:]:
                    if y + fs > bh:
                        break
                    d.text((ox + pad_n, oy + y), "\u2713 " + clean_for(item_f, it), font=item_f, fill=INK + (255,))
                    y += fs * 1.3
        elif variant == "route":
            font = _font("Inter", round(fs), 400, 14)
            stops = [r for r in txt.split("\n") if r.strip()]
            x_line = ox + self.w * 0.006
            step = fs * 1.2 + self.w * 0.016
            if len(stops) > 1:
                d.line((x_line, oy + fs * 0.7, x_line, oy + fs * 0.7 + step * (len(stops) - 1)), fill=TERRACOTTA + (255,), width=max(2, round(self.w * 0.003)))
            for i, s in enumerate(stops):
                cy = oy + i * step
                if cy + fs > bh + pad:
                    break
                r = self.w * 0.0075
                d.ellipse((x_line - r, cy + fs * 0.7 - r, x_line + r, cy + fs * 0.7 + r), fill=TERRACOTTA + (255,))
                d.text((ox + self.w * 0.024, cy), clean_for(font, s), font=font, fill=INK + (255,))
        elif variant == "quote":
            font = _font("PlayfairDisplay-Italic", round(fs), 400)
            mark_f = _font("PlayfairDisplay", round(fs * 3.0), 700)
            colour = IVORY if st.get("tone") == "light" else white
            txt = clean_for(font, " ".join(txt.split()))
            lines = _wrap(d, txt, font, bw)
            lh = fs * LINE_H["quote"]
            block = fs * 1.9 + len(lines) * lh + fs * 0.9
            y = max(0.0, (bh - block) / 2)
            mw = d.textlength("\u201c", font=mark_f)
            d.text((ox + ((bw - mw) / 2 if centre else 0), oy + y - fs * 0.5), "\u201c", font=mark_f, fill=TERRACOTTA + (255,))
            y += fs * 1.9
            end = draw_lines(lines, font, colour, lh, shadow=on_photo, start_y=y)
            rw = bw * 0.14
            d.line((ox + (bw - rw) / 2, oy + end + fs * 0.35, ox + (bw + rw) / 2, oy + end + fs * 0.35), fill=TERRACOTTA + (255,), width=max(2, round(self.w * 0.002)))
        elif variant == "map":
            self._map(d, layer, ox, oy, bw, bh, txt, fs)
        else:  # body
            font = _font("Inter", round(fs), 400, 14)
            txt = clean_for(font, " ".join(txt.split()) if "\n" not in txt else txt)
            draw_lines(_wrap(d, txt, font, bw), font, white if on_photo else INK, fs * LINE_H["body"], shadow=on_photo)

        rot = float(st.get("rotate") or 0)
        if rot:
            layer = layer.rotate(-rot, resample=Image.Resampling.BICUBIC)
        # clip to the text box (plus the small padding), like overflow:hidden in the preview
        clip = Image.new("L", layer.size, 0)
        ImageDraw.Draw(clip).rectangle((0, 0, layer.width, oy + bh + pad), fill=255)
        layer.putalpha(Image.composite(layer.getchannel("A"), Image.new("L", layer.size, 0), clip))
        canvas.paste(layer, (x0 + self.bleed - pad, y0 + self.bleed - pad), layer)

    # ----- illustrated route map: drawn offline from the stops' coordinates (no map tiles, no network) -----
    def _map(self, d: ImageDraw.ImageDraw, layer: Image.Image, ox: int, oy: int, bw: int, bh: int, txt: str, fs: float) -> None:
        stops = []
        for row in txt.split("\n"):
            name, _, rest = row.partition("|")
            lat, _, lon = rest.partition("|")
            try:
                stops.append((name.strip(), float(lat), float(lon)))
            except ValueError:
                continue
        if len(stops) < 2:
            return
        u = self.w / 100
        land, edge = (248, 240, 220, 255), TERRACOTTA_DEEP + (255,)
        rnd = random.Random("|".join(s[0] for s in stops))
        # paper card with a double border
        d.rounded_rectangle((ox, oy, ox + bw, oy + bh), radius=round(u * 1.2), fill=land, outline=edge, width=max(2, round(u * 0.25)))
        inset = round(u * 0.9)
        d.rounded_rectangle((ox + inset, oy + inset, ox + bw - inset, oy + bh - inset), radius=round(u * 0.8), outline=TERRACOTTA + (150,), width=max(1, round(u * 0.12)))
        # soft terrain: a few blurred blobs, then a graticule on top
        terrain = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        td = ImageDraw.Draw(terrain)
        for _ in range(9):
            cx, cy, r = rnd.uniform(0.1, 0.9) * bw, rnd.uniform(0.1, 0.9) * bh, rnd.uniform(0.10, 0.24) * min(bw, bh)
            td.ellipse((cx - r, cy - r * 0.7, cx + r, cy + r * 0.7), fill=rnd.choice([(196, 213, 178, 80), (222, 208, 170, 70), (178, 205, 214, 60)]))
        terrain = terrain.filter(ImageFilter.GaussianBlur(min(bw, bh) * 0.03))
        mask = Image.new("L", (bw, bh), 0)
        ImageDraw.Draw(mask).rounded_rectangle((inset, inset, bw - inset, bh - inset), radius=round(u * 0.8), fill=255)
        terrain.putalpha(ImageChops.multiply(terrain.getchannel("A"), mask))
        layer.alpha_composite(terrain, (ox, oy))
        d = ImageDraw.Draw(layer)
        for k in range(1, 6):
            gx, gy = ox + bw * k / 6, oy + bh * k / 6
            d.line((gx, oy + inset, gx, oy + bh - inset), fill=TERRACOTTA + (45,), width=1)
            d.line((ox + inset, gy, ox + bw - inset, gy), fill=TERRACOTTA + (45,), width=1)

        # projection: equirectangular with a cos(latitude) correction; very thin routes are widened so the shape reads well
        lats, lons = [s[1] for s in stops], [s[2] for s in stops]
        mid = math.radians((min(lats) + max(lats)) / 2)
        xs, ys = [lon * math.cos(mid) for lon in lons], [-lat for lat in lats]
        sx, sy = max(max(xs) - min(xs), 0.02), max(max(ys) - min(ys), 0.02)
        if sx < sy * 0.5:
            sx = sy * 0.5
        elif sy < sx * 0.5:
            sy = sx * 0.5
        mx, my = bw * 0.20, bh * 0.16
        scale = min((bw - 2 * mx) / sx, (bh - 2 * my) / sy)
        cxm, cym = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        pts = [(ox + bw / 2 + (x - cxm) * scale, oy + bh / 2 + (y - cym) * scale) for x, y in zip(xs, ys)]

        # dashed, gently curved route
        for i in range(len(pts) - 1):
            (x0, y0), (x1, y1) = pts[i], pts[i + 1]
            dx, dy = x1 - x0, y1 - y0
            ln = math.hypot(dx, dy) or 1.0
            bend = (0.16 if i % 2 == 0 else -0.16) * ln
            qx, qy = (x0 + x1) / 2 - dy / ln * bend, (y0 + y1) / 2 + dx / ln * bend
            steps = max(12, int(ln / (u * 0.5)))
            curve = [((1 - t) ** 2 * x0 + 2 * (1 - t) * t * qx + t * t * x1, (1 - t) ** 2 * y0 + 2 * (1 - t) * t * qy + t * t * y1)
                     for t in (k / steps for k in range(steps + 1))]
            for k in range(0, len(curve) - 1, 2):
                d.line((curve[k], curve[k + 1]), fill=TERRACOTTA_DEEP + (255,), width=max(2, round(u * 0.32)))

        # pins and labels
        label_f = _font("Caveat", round(fs * 1.55), 700)
        num_f = _font("Inter", round(fs * 0.8), 700, 14)
        taken: list[tuple[float, float, float, float]] = []
        r = u * 1.35
        for i, ((name, _, _), (px, py)) in enumerate(zip(stops, pts), 1):
            d.ellipse((px - r + 2, py - r + 3, px + r + 2, py + r + 3), fill=(31, 42, 38, 70))
            d.ellipse((px - r, py - r, px + r, py + r), fill=TERRACOTTA + (255,), outline=(255, 255, 255, 255), width=max(2, round(u * 0.22)))
            num = str(i)
            d.text((px - d.textlength(num, font=num_f) / 2, py - fs * 0.52), num, font=num_f, fill=(255, 255, 255, 255))
            label = clean_for(label_f, name)
            lw, lh = d.textlength(label, font=label_f), fs * 1.55
            right = px < ox + bw * 0.58
            for dy in (0, -1.3, 1.3, -2.4, 2.4):
                lx = px + r + u * 0.7 if right else px - r - u * 0.7 - lw
                ly = py - lh / 2 + dy * lh
                box = (lx - 3, ly, lx + lw + 3, ly + lh)
                if all(box[2] < t[0] or box[0] > t[2] or box[3] < t[1] or box[1] > t[3] for t in taken) and ox + inset <= box[0] and box[2] <= ox + bw - inset:
                    break
            taken.append(box)
            d.text((lx, ly - lh * 0.12), label, font=label_f, fill=INK + (255,))

        # compass
        cx, cy, cr = ox + bw - u * 4.2, oy + u * 4.6, u * 2.2
        d.polygon([(cx, cy - cr), (cx + cr * 0.28, cy), (cx, cy + cr), (cx - cr * 0.28, cy)], fill=TERRACOTTA_DEEP + (210,))
        d.polygon([(cx - cr, cy), (cx, cy - cr * 0.28), (cx + cr, cy), (cx, cy + cr * 0.28)], fill=TERRACOTTA + (150,))
        n_f = _font("Inter", round(fs * 0.8), 700, 14)
        d.text((cx - d.textlength("N", font=n_f) / 2, cy - cr - fs * 1.15), "N", font=n_f, fill=INK + (255,))

    @staticmethod
    def _text_at(d, xy, text, font, fill, spacing):
        if not spacing:
            d.text(xy, text, font=font, fill=fill)
            return
        x, y = xy
        for ch in text:
            d.text((x, y), ch, font=font, fill=fill)
            x += d.textlength(ch, font=font) + spacing


def _wrap_spaced(draw, text, font, width, spacing):
    lines, cur = [], ""
    for w in text.split():
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) + spacing * len(trial) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def to_jpeg(img: Image.Image, quality: int = 88) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=quality, optimize=True, subsampling=0)
    return buf.getvalue()


def page_pdf(img: Image.Image, dpi: int, quality: int = 88) -> bytes:
    """One page as a one-page PDF with the picture embedded as a JPEG (kept small; merged later page by page)."""
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PDF", resolution=float(dpi), quality=quality)
    return buf.getvalue()


def merge_pdfs(pages: list[bytes], dpi: int, bleed_px: int) -> bytes:
    """Merge one-page PDFs and set BleedBox/TrimBox so a printer can see where the page will be cut."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import RectangleObject

    writer = PdfWriter()
    for data in pages:
        writer.add_page(PdfReader(io.BytesIO(data)).pages[0])
    pt = 72.0 / dpi
    for page in writer.pages:
        W, H, b = float(page.mediabox.width), float(page.mediabox.height), bleed_px * pt
        page.bleedbox = RectangleObject([0, 0, W, H])
        page.trimbox = RectangleObject([b, b, W - b, H - b])
    writer.add_metadata({"/Title": "WoDin photobook", "/Producer": "WoDin"})
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
