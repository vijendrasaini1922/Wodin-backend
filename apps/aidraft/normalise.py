"""Photo normalisation before anything is sent to an AI service.

Every photo is turned into the same boring, safe shape: upright, RGB, sRGB colour, at most `edge` px on the long side,
JPEG, with ALL metadata (EXIF, GPS, camera, ICC, XMP) dropped because a fresh JPEG is encoded without it.
"""
import io

from PIL import Image, ImageCms, ImageOps


def normalise_for_ai(data: bytes, edge: int = 1024, quality: int = 80) -> tuple[bytes, tuple[int, int]]:
    with Image.open(io.BytesIO(data)) as src:
        src.load()
        icc = src.info.get("icc_profile")
        img = ImageOps.exif_transpose(src)  # make the pixels upright, then the orientation tag no longer matters

        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            rgba = img.convert("RGBA")
            flat = Image.new("RGB", rgba.size, "white")
            flat.paste(rgba, mask=rgba.getchannel("A"))
            img = flat
        else:
            img = img.convert("RGB")

        if icc:  # wide-gamut phone photos (Display P3) look dull if treated as sRGB
            try:
                img = ImageCms.profileToProfile(
                    img, ImageCms.ImageCmsProfile(io.BytesIO(icc)), ImageCms.createProfile("sRGB"), outputMode="RGB")
            except Exception:  # noqa: BLE001 - a broken profile must not lose the photo
                pass

        img.thumbnail((edge, edge), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        img.save(out, "JPEG", quality=quality, optimize=True)  # no exif=/icc_profile= -> metadata not carried over
        return out.getvalue(), img.size
