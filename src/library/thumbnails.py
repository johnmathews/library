"""First-page thumbnail rendering: WebP, ~480 px wide, in the derived dir.

PDFs render their first page via pypdfium2; images load via Pillow (HEIC
through the derived ``converted.jpg`` written at ingest, so no HEIF decode
is needed here). Plain text has no visual — ``render_thumbnail`` returns
``None`` and no artifact is written.

Thumbnail presence is marked purely by the existence of ``thumb.webp`` in
the document's derived directory (no database column).
"""

from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageOps

from library.images import CONVERTED_JPEG_NAME, HEIC_MIME_TYPES

THUMBNAIL_NAME: str = "thumb.webp"
THUMBNAIL_WIDTH: int = 480
WEBP_QUALITY: int = 80

# Types Pillow opens directly from the original file.
PILLOW_MIME_TYPES: frozenset[str] = frozenset({"image/jpeg", "image/png", "image/tiff"})


def render_thumbnail(mime_type: str, original: Path, derived: Path) -> Path | None:
    """Write ``thumb.webp`` into ``derived``; ``None`` for types with no visual."""
    if mime_type == "application/pdf":
        image = _render_pdf_first_page(original)
    elif mime_type in HEIC_MIME_TYPES:
        image = Image.open(derived / CONVERTED_JPEG_NAME)
    elif mime_type in PILLOW_MIME_TYPES:
        image = Image.open(original)
    else:
        return None
    target = derived / THUMBNAIL_NAME
    _write_webp(image, target)
    return target


def _render_pdf_first_page(pdf_path: Path) -> Image.Image:
    """Rasterize page 1 at a scale that lands close to the target width."""
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        page = document[0]
        width_pt, _ = page.get_size()
        scale = THUMBNAIL_WIDTH / width_pt if width_pt else 1.0
        return page.render(scale=scale).to_pil()
    finally:
        document.close()


def _write_webp(image: Image.Image, target: Path) -> None:
    """Downscale to at most THUMBNAIL_WIDTH wide (never upscale) and save WebP."""
    upright = ImageOps.exif_transpose(image) or image
    if upright.mode not in ("RGB", "RGBA", "L"):
        upright = upright.convert("RGB")
    if upright.width > THUMBNAIL_WIDTH:
        height = max(1, round(upright.height * THUMBNAIL_WIDTH / upright.width))
        upright = upright.resize((THUMBNAIL_WIDTH, height), Image.Resampling.LANCZOS)
    upright.save(target, format="WEBP", quality=WEBP_QUALITY, method=4)
