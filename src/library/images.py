"""Image normalisation: HEIC/HEIF to JPEG conversion.

The original HEIC bytes remain the content-addressed source of truth; the
JPEG produced here is stored as a derived artifact (``converted.jpg``) and is
what downstream steps (OCR, thumbnails) consume.
"""

import io
from typing import NamedTuple

import pillow_heif
from PIL import Image, ImageOps

pillow_heif.register_heif_opener()

HEIC_MIME_TYPES: frozenset[str] = frozenset({"image/heic", "image/heif"})
JPEG_QUALITY: int = 90
# Name of the derived JPEG artifact written for HEIC originals; consumed by
# downstream steps (OCR photo path, thumbnails). Lives here (not ingest.py)
# so the OCR router can import it without a circular import via jobs.
CONVERTED_JPEG_NAME: str = "converted.jpg"


class NormalizedImage(NamedTuple):
    """Result of image normalisation."""

    content: bytes
    mime: str
    converted: bool


def normalize_image(content: bytes, mime: str) -> NormalizedImage:
    """Convert HEIC/HEIF content to an upright JPEG; pass anything else through.

    Orientation is baked into the pixels: pillow-heif applies HEIF rotation
    properties on decode, and ``ImageOps.exif_transpose`` applies (and clears)
    any remaining EXIF orientation tag.
    """
    if mime not in HEIC_MIME_TYPES:
        return NormalizedImage(content, mime, converted=False)

    image = Image.open(io.BytesIO(content))
    upright = ImageOps.exif_transpose(image)
    if upright.mode not in ("RGB", "L"):
        upright = upright.convert("RGB")
    buffer = io.BytesIO()
    upright.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return NormalizedImage(buffer.getvalue(), "image/jpeg", converted=True)
