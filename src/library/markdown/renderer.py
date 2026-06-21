"""Rasterize a document's pages to JPEG bytes for vision markdown generation.

Mirrors the existing pypdfium2/Pillow usage in ``library.thumbnails`` and the
OCR confidence probe. PDFs render each page; single images render once; HEIC
uses the derived ``converted.jpg``; TIFF uses the OCR-produced
``searchable.pdf``; ``text/plain`` has no visual and yields no images.

Security hardening: both the PDF rasteriser and the Pillow image branches are
bounded so that untrusted uploads cannot trigger resource exhaustion.

  - PDF pages are rendered at a scale that targets ``long_side_px`` directly,
    capped at ``_PDF_RENDER_SCALE``.  A maliciously huge page (large point
    dimensions) therefore never produces a bitmap much larger than the intended
    output size.

  - Pillow image branches enforce a pixel-count budget (``_MAX_IMAGE_PIXELS``).
    Images whose decoded size would exceed the budget are rejected (return
    ``[]``) and a ``PIL.Image.DecompressionBombError`` is caught and treated
    the same way.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

log = logging.getLogger(__name__)

# Render PDFs at this scale relative to the PDF's point size before the
# long-side downscale; 2.0 (~144 dpi) is the *upper* bound — pages are
# rendered closer to the target when their point dimensions are large.
_PDF_RENDER_SCALE: float = 2.0

# Maximum pixel budget for Pillow-decoded images (~40 MP; comfortably above
# any real scanned page, well below a decompression-bomb payload).
_MAX_IMAGE_PIXELS: int = 40_000_000


def _encode(image: Image.Image, long_side_px: int) -> bytes:
    rgb = image.convert("RGB")
    longest = max(rgb.size)
    if longest > long_side_px:
        ratio = long_side_px / longest
        rgb = rgb.resize((round(rgb.width * ratio), round(rgb.height * ratio)))
    buffer = io.BytesIO()
    rgb.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


def _render_pdf(path: Path, *, max_pages: int, long_side_px: int) -> list[bytes]:
    pdf = pdfium.PdfDocument(str(path))
    try:
        count = min(len(pdf), max_pages)
        images: list[bytes] = []
        for index in range(count):
            page = pdf[index]
            width_pt, height_pt = page.get_size()
            long_side_pt = max(width_pt, height_pt)
            scale = (
                min(_PDF_RENDER_SCALE, long_side_px / long_side_pt)
                if long_side_pt > 0
                else 0.5  # degenerate empty page — render at a safe minimum
            )
            bitmap = page.render(scale=scale)
            images.append(_encode(bitmap.to_pil(), long_side_px))
        return images
    finally:
        pdf.close()


def _open_image_safe(path: Path) -> Image.Image | None:
    """Open *path* with Pillow, enforcing the pixel budget.

    Returns ``None`` and logs a warning when the image exceeds
    ``_MAX_IMAGE_PIXELS`` or raises ``DecompressionBombError``.
    """
    try:
        img = Image.open(path)
        # .size is read from the file header without decoding pixel data.
        width, height = img.size
        if width * height > _MAX_IMAGE_PIXELS:
            log.warning(
                "renderer: skipping oversize image %s (%dx%d = %d px > budget %d)",
                path,
                width,
                height,
                width * height,
                _MAX_IMAGE_PIXELS,
            )
            return None
        return img
    except Image.DecompressionBombError:
        log.warning("renderer: decompression-bomb detected in %s — skipping", path)
        return None


def render_page_images(
    mime_type: str,
    original_path: Path,
    derived: Path,
    *,
    max_pages: int,
    long_side_px: int,
) -> list[bytes]:
    """One JPEG per page (in order), capped at ``max_pages``; ``[]`` when none."""
    if mime_type == "application/pdf":
        return _render_pdf(original_path, max_pages=max_pages, long_side_px=long_side_px)
    if mime_type == "image/tiff":
        searchable = derived / "searchable.pdf"
        if not searchable.exists():
            return []
        return _render_pdf(searchable, max_pages=max_pages, long_side_px=long_side_px)
    if mime_type in ("image/jpeg", "image/png"):
        img = _open_image_safe(original_path)
        if img is None:
            return []
        return [_encode(img, long_side_px)]
    if mime_type in ("image/heic", "image/heif"):
        converted = derived / "converted.jpg"
        if not converted.exists():
            return []
        img = _open_image_safe(converted)
        if img is None:
            return []
        return [_encode(img, long_side_px)]
    return []  # text/plain and anything else: no renderable visual
