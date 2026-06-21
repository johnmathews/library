"""Rasterize a document's pages to JPEG bytes for vision markdown generation.

Mirrors the existing pypdfium2/Pillow usage in ``library.thumbnails`` and the
OCR confidence probe. PDFs render each page; single images render once; HEIC
uses the derived ``converted.jpg``; TIFF uses the OCR-produced
``searchable.pdf``; ``text/plain`` has no visual and yields no images.
"""

from __future__ import annotations

import io
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

# Render PDFs at this scale relative to the PDF's point size before the
# long-side downscale; 2.0 (~144 dpi) is plenty for table fidelity.
_PDF_RENDER_SCALE: float = 2.0


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
            bitmap = page.render(scale=_PDF_RENDER_SCALE)
            images.append(_encode(bitmap.to_pil(), long_side_px))
        return images
    finally:
        pdf.close()


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
        return [_encode(Image.open(original_path), long_side_px)]
    if mime_type in ("image/heic", "image/heif"):
        converted = derived / "converted.jpg"
        if not converted.exists():
            return []
        return [_encode(Image.open(converted), long_side_px)]
    return []  # text/plain and anything else: no renderable visual
