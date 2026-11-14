"""Image processing for the import pipeline (pure functions, no I/O).

process() takes the bytes of an uploaded photo and returns everything the
importer needs: display dimensions, EXIF date taken, resized WebP variants,
a tiny blur-up placeholder and an original with GPS location stripped.
"""
import base64
import io
from dataclasses import dataclass
from datetime import datetime

from PIL import Image, ImageOps

SIZES = (400, 800, 1600)
WEBP_QUALITY = 82
EXIF_IFD = 0x8769
GPS_IFD = 0x8825
TAG_DATETIME_ORIGINAL = 0x9003
TAG_DATETIME = 0x0132

EXT = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
CONTENT_TYPE = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


class UnsupportedImage(ValueError):
    pass


@dataclass
class Processed:
    width: int  # display dimensions, after applying EXIF orientation
    height: int
    taken_at: str | None  # ISO 8601, from EXIF
    placeholder: str  # data: URI of a tiny blurred WebP
    variants: dict[int, bytes]  # width -> WebP bytes
    original: bytes  # GPS stripped
    original_ext: str


def _taken_at(exif):
    raw = exif.get_ifd(EXIF_IFD).get(TAG_DATETIME_ORIGINAL) or exif.get(TAG_DATETIME)
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw).strip("\x00 "), "%Y:%m:%d %H:%M:%S").isoformat()
    except ValueError:
        return None


def _has_gps(exif):
    return bool(exif.get_ifd(GPS_IFD))


def _strip_gps(img, exif, fmt):
    """Re-save the original without GPS. Orientation is kept (not baked in)."""
    del exif[GPS_IFD]
    buf = io.BytesIO()
    kwargs = {"exif": exif.tobytes()}
    if img.info.get("icc_profile"):
        kwargs["icc_profile"] = img.info["icc_profile"]
    if fmt == "JPEG":
        kwargs.update(quality=95, subsampling="keep")
    elif fmt == "WEBP":
        kwargs.update(quality=95)
    img.save(buf, fmt, **kwargs)
    return buf.getvalue()


def _to_webp(img, quality=WEBP_QUALITY):
    buf = io.BytesIO()
    kwargs = {"quality": quality, "method": 4}
    if img.info.get("icc_profile"):
        kwargs["icc_profile"] = img.info["icc_profile"]
    img.save(buf, "WEBP", **kwargs)
    return buf.getvalue()


def process(data):
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:  # corrupt or unknown format
        raise UnsupportedImage(f"cannot read image: {e}") from e
    fmt = img.format
    if fmt not in EXT:
        raise UnsupportedImage(f"unsupported format: {fmt}")

    exif = img.getexif()
    taken_at = _taken_at(exif)
    original = _strip_gps(img, exif, fmt) if _has_gps(exif) else data

    oriented = ImageOps.exif_transpose(img)
    oriented = oriented.convert("RGBA" if oriented.mode in ("RGBA", "LA", "PA") else "RGB")
    w, h = oriented.size

    widths = [s for s in SIZES if s <= w] or [w]  # never upscale
    variants = {}
    for s in widths:
        resized = oriented if s == w else oriented.resize((s, round(h * s / w)), Image.LANCZOS)
        variants[s] = _to_webp(resized)

    tiny = oriented.copy()
    tiny.thumbnail((24, 24))
    tiny.info.pop("icc_profile", None)
    b64 = base64.b64encode(_to_webp(tiny, quality=30)).decode()
    return Processed(w, h, taken_at, f"data:image/webp;base64,{b64}", variants, original, EXT[fmt])
