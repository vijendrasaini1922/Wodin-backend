"""Print-ready PDF generation from the approved revision's structured page data and ORIGINAL photos.

Never built from browser screenshots. Each page is rendered at 300 DPI at trim size + bleed. Text is rasterised
(Pillow cannot embed fonts); if the printer requires embedded fonts, replace this module with a ReportLab/WeasyPrint
renderer - the validation report below stays the same.
"""
import io

from PIL import Image, ImageDraw, ImageFont, ImageOps

from apps.photos.models import Photo
from apps.photos.processing import apply_edits

DPI = 300
MM_PER_INCH = 25.4
MIN_EFFECTIVE_DPI_ERROR = 100
MIN_EFFECTIVE_DPI_WARN = 200
SAFE_MARGIN_MM = 5


def mm_to_px(mm) -> int:
    return round(float(mm) / MM_PER_INCH * DPI)


def _cover_fit(img: Image.Image, w: int, h: int) -> Image.Image:
    return ImageOps.fit(img, (max(w, 1), max(h, 1)), Image.Resampling.LANCZOS)


def build(revision, variant, storage) -> tuple[bytes, dict]:
    """Return (pdf_bytes, report). report['errors'] non-empty means the file must not be used for production."""
    trim_w, trim_h, bleed = float(variant.trim_width_mm), float(variant.trim_height_mm), float(variant.bleed_mm)
    page_w, page_h = mm_to_px(trim_w + 2 * bleed), mm_to_px(trim_h + 2 * bleed)
    bleed_px = mm_to_px(bleed)
    snapshot = revision.snapshot
    errors, warnings, pages_out = [], [], []

    numbers = sorted(p["number"] for p in snapshot["pages"])
    expected = list(range(0, snapshot["page_count"] + 1))  # cover + inner pages
    if numbers != expected:
        missing = sorted(set(expected) - set(numbers))
        errors.append(f"Page order/count mismatch: expected pages 0..{snapshot['page_count']}, missing {missing}.")

    photo_ids = {e["photo_id"] for p in snapshot["pages"] for e in p["elements"] if e["photo_id"]}
    photos = {str(p.id): p for p in Photo.objects.filter(pk__in=photo_ids)}
    cache: dict[str, Image.Image] = {}

    try:
        font = ImageFont.load_default(size=int(DPI / 6))
    except TypeError:  # very old Pillow
        font = ImageFont.load_default()

    for page in sorted(snapshot["pages"], key=lambda p: p["number"]):
        canvas = Image.new("RGB", (page_w, page_h), "white")
        draw = ImageDraw.Draw(canvas)
        for el in sorted(page["elements"], key=lambda e: e["z"]):
            # element geometry is relative to the trim box
            x = bleed_px + round(el["x"] * mm_to_px(trim_w))
            y = bleed_px + round(el["y"] * mm_to_px(trim_h))
            w, h = round(el["w"] * mm_to_px(trim_w)), round(el["h"] * mm_to_px(trim_h))
            # elements touching the trim edge extend into the bleed
            if el["x"] <= 0.001: x, w = 0, w + bleed_px
            if el["y"] <= 0.001: y, h = 0, h + bleed_px
            if el["x"] + el["w"] >= 0.999: w = page_w - x
            if el["y"] + el["h"] >= 0.999: h = page_h - y
            if el["kind"] == "photo":
                photo = photos.get(el["photo_id"])
                if not photo or photo.status != Photo.Status.READY or not storage.exists(photo.original_key):
                    errors.append(f"Page {page['number']}: photo is missing or unavailable.")
                    continue
                if el["photo_id"] not in cache:
                    with Image.open(io.BytesIO(storage.read(photo.original_key))) as src:
                        src.load()
                        cache[el["photo_id"]] = apply_edits(src, photo.rotation, photo.crop).convert("RGB")
                img = cache[el["photo_id"]]
                # effective resolution of the photo as placed (pixels per inch of printed area)
                scale = min(img.width / max(w, 1), img.height / max(h, 1))  # source px per output px (cover fit)
                eff_dpi = DPI * scale
                if eff_dpi < MIN_EFFECTIVE_DPI_ERROR:
                    errors.append(f"Page {page['number']}: photo resolution too low ({eff_dpi:.0f} DPI).")
                elif eff_dpi < MIN_EFFECTIVE_DPI_WARN:
                    warnings.append(f"Page {page['number']}: photo is {eff_dpi:.0f} DPI (below {MIN_EFFECTIVE_DPI_WARN}).")
                canvas.paste(_cover_fit(img, w, h), (x, y))
            else:
                inside = (x >= bleed_px + mm_to_px(SAFE_MARGIN_MM) - 1 and y >= bleed_px + mm_to_px(SAFE_MARGIN_MM) - 1
                          and x + w <= page_w - bleed_px - mm_to_px(SAFE_MARGIN_MM) + 1
                          and y + h <= page_h - bleed_px - mm_to_px(SAFE_MARGIN_MM) + 1)
                if not inside:
                    warnings.append(f"Page {page['number']}: text is outside the {SAFE_MARGIN_MM} mm safe margin.")
                draw.text((x, y), el.get("text", ""), fill=(30, 30, 30), font=font)
        pages_out.append(canvas)

    report = {
        "trim_mm": [trim_w, trim_h], "bleed_mm": bleed, "dpi": DPI, "pixel_size": [page_w, page_h],
        "page_count": len(pages_out), "errors": errors, "warnings": warnings,
        "fonts_embedded": False, "color_space": "RGB (convert to the printer's profile before sending)",
    }
    if not pages_out:
        errors.append("No pages to export.")
        return b"", report
    buf = io.BytesIO()
    pages_out[0].save(buf, "PDF", resolution=float(DPI), save_all=True, append_images=pages_out[1:])
    return buf.getvalue(), report
