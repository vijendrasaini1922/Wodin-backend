"""Photo validation, metadata extraction, quality flags and derivative generation (Pillow)."""
import hashlib
import io
from datetime import datetime

from django.conf import settings
from django.utils import timezone
from PIL import Image, ImageFilter, ImageOps, ImageStat

try:  # HEIC/HEIF support
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:  # pragma: no cover
    pillow_heif = None

ALLOWED_FORMATS = {"JPEG": {"image/jpeg"}, "PNG": {"image/png"}, "HEIF": {"image/heic", "image/heif"}}
EXT_BY_TYPE = {"image/jpeg": "jpg", "image/png": "png", "image/heic": "heic", "image/heif": "heic"}
TYPE_BY_EXT = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "heic": "image/heic", "heif": "image/heif"}
THUMB_SIZE, PREVIEW_SIZE = 400, 1600


class PhotoRejected(Exception):
    pass


def declared_type(filename: str, content_type: str) -> str | None:
    ct = (content_type or "").lower()
    if ct in EXT_BY_TYPE:
        return ct
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return TYPE_BY_EXT.get(ext)


def dhash(img: Image.Image) -> str:
    small = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    px = list(small.getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (px[row * 9 + col] > px[row * 9 + col + 1])
    return f"{bits:016x}"


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def blur_score(img: Image.Image) -> float:
    """Variance of the Laplacian on a downscaled greyscale copy. Low = blurry."""
    gray = img.convert("L")
    gray.thumbnail((512, 512))
    lap = gray.filter(ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128))
    return float(ImageStat.Stat(lap).var[0])


def _taken_at(img: Image.Image):
    try:
        exif = img.getexif()
        raw = exif.get_ifd(0x8769).get(36867) or exif.get(306)
        if raw:
            naive = datetime.strptime(str(raw), "%Y:%m:%d %H:%M:%S")
            return timezone.make_aware(naive)
    except Exception:
        pass
    return None


def apply_edits(img: Image.Image, rotation: int, crop: dict | None) -> Image.Image:
    img = ImageOps.exif_transpose(img)
    if rotation:
        img = img.rotate(-rotation, expand=True)  # PIL rotates counter-clockwise
    if crop:
        w, h = img.size
        x, y, cw, ch = (float(crop[k]) for k in ("x", "y", "w", "h"))
        box = (int(x * w), int(y * h), int((x + cw) * w), int((y + ch) * h))
        if box[2] - box[0] > 10 and box[3] - box[1] > 10:
            img = img.crop(box)
    return img


def _jpeg_bytes(img: Image.Image, max_side: int, quality: int) -> bytes:
    out = img.convert("RGB")
    out.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def build_derivatives(photo, storage, original: bytes | None = None) -> None:
    """(Re)generate thumb + preview JPEGs from the untouched original with the customer's edits applied."""
    original = original if original is not None else storage.read(photo.original_key)
    with Image.open(io.BytesIO(original)) as img:
        img.load()
        edited = apply_edits(img, photo.rotation, photo.crop)
        base = photo.original_key.rsplit("/", 1)[0]
        photo.thumb_key, photo.preview_key = f"{base}/thumb.jpg", f"{base}/preview.jpg"
        storage.save(photo.thumb_key, _jpeg_bytes(edited, THUMB_SIZE, 80), "image/jpeg")
        storage.save(photo.preview_key, _jpeg_bytes(edited, PREVIEW_SIZE, 85), "image/jpeg")


def process(photo, storage) -> None:
    """Validate the uploaded original and fill metadata/flags. Raises PhotoRejected for unusable files."""
    from .models import Photo

    data = storage.read(photo.original_key)
    if len(data) > settings.MAX_UPLOAD_BYTES:
        raise PhotoRejected("File is too large.")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            fmt = probe.format
            probe.verify()
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        raise PhotoRejected("File is corrupted or not a supported image.")
    if fmt not in ALLOWED_FORMATS or photo.content_type not in ALLOWED_FORMATS[fmt]:
        raise PhotoRejected("File content does not match its type. Use JPG, PNG or HEIC.")

    oriented = ImageOps.exif_transpose(img)
    width, height = oriented.size
    if min(width, height) < settings.REJECT_PHOTO_PIXELS:
        raise PhotoRejected("Image resolution is too low to print.")

    flags = []
    if min(width, height) < settings.MIN_PHOTO_PIXELS:
        flags.append("low_resolution")
    score = blur_score(oriented)
    if score < settings.BLUR_THRESHOLD:
        flags.append("blurry")

    digest, hash_ = hashlib.sha256(data).hexdigest(), dhash(oriented)
    others = Photo.objects.filter(trip=photo.trip, status=Photo.Status.READY).exclude(pk=photo.pk).order_by("created_at")
    duplicate_of = None
    exact = others.filter(sha256=digest).first()
    if exact:
        duplicate_of, flags = exact, flags + ["duplicate"]
    else:
        for other in others.exclude(dhash=""):
            if hamming(other.dhash, hash_) <= settings.NEAR_DUPLICATE_DISTANCE:
                duplicate_of, flags = other, flags + ["near_duplicate"]
                break

    photo.width, photo.height = width, height
    photo.taken_at = _taken_at(img)
    photo.sha256, photo.dhash, photo.blur_score = digest, hash_, round(score, 2)
    photo.flags, photo.duplicate_of = flags, duplicate_of
    photo.size_bytes = len(data)
    build_derivatives(photo, storage, data)
    photo.status = Photo.Status.READY
