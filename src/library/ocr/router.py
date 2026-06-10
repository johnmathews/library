"""OCR router: pick the right engine for a document and apply the gate.

Routing (see docs/ingestion.md):

- ``text/plain``                  -> passthrough read (engine "text")
- ``application/pdf`` w/ text layer -> pypdfium2 extraction (engine "text-layer")
- ``application/pdf`` image-only  -> Tesseract path + confidence gate
- ``image/tiff``                  -> wrapped into a PDF, then Tesseract path
- ``image/jpeg``/``image/png``    -> photo path (OpenCV + RapidOCR)
- ``image/heic``/``image/heif``   -> photo path on the derived converted.jpg

Confidence gate: a Tesseract result below ``ocr_confidence_threshold`` (or
with no measurable confidence) is retried through the photo path on the
rasterized pages; the higher-confidence result wins, but the Tesseract
``searchable.pdf`` artifact is kept as the viewing artifact either way.
"""

from dataclasses import replace
from pathlib import Path

import pypdfium2 as pdfium

from library.config import Settings, get_settings
from library.images import CONVERTED_JPEG_NAME, HEIC_MIME_TYPES
from library.models import Document
from library.ocr import photo, tesseract
from library.ocr.base import OcrResult

PHOTO_MIME_TYPES: frozenset[str] = frozenset({"image/jpeg", "image/png"})


class UnsupportedOcrInputError(ValueError):
    """The document's MIME type has no OCR route."""

    def __init__(self, mime_type: str) -> None:
        self.mime_type = mime_type
        super().__init__(f"no OCR route for mime type: {mime_type!r}")


def run_ocr(
    document: Document,
    original_path: Path,
    derived: Path,
    *,
    settings: Settings | None = None,
) -> OcrResult:
    """Route the document to the right OCR engine and return its result."""
    settings = settings if settings is not None else get_settings()
    mime_type = document.mime_type

    if mime_type == "text/plain":
        text = original_path.read_text(encoding="utf-8", errors="replace").strip()
        return OcrResult(text=text, confidence=None, searchable_pdf=None, engine="text", pages=None)

    if mime_type == "application/pdf":
        text, pages = extract_text_layer(original_path)
        if pages > 0 and len(text) / pages >= settings.text_layer_min_chars_per_page:
            return OcrResult(
                text=text,
                confidence=None,
                searchable_pdf=None,
                engine="text-layer",
                pages=pages,
            )
        return _tesseract_with_gate(original_path, derived, settings)

    if mime_type == "image/tiff":
        pdf_path = tesseract.tiff_to_pdf(original_path, derived)
        return _tesseract_with_gate(pdf_path, derived, settings)

    if mime_type in HEIC_MIME_TYPES:
        return photo.ocr_image(derived / CONVERTED_JPEG_NAME)

    if mime_type in PHOTO_MIME_TYPES:
        return photo.ocr_image(original_path)

    raise UnsupportedOcrInputError(mime_type)


def extract_text_layer(pdf_path: Path) -> tuple[str, int]:
    """All text from a PDF's text layer plus its page count."""
    parts: list[str] = []
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        pages = len(document)
        for index in range(pages):
            text_page = document[index].get_textpage()
            try:
                parts.append(text_page.get_text_bounded() or "")
            finally:
                text_page.close()
    finally:
        document.close()
    return "\n\n".join(part.strip() for part in parts).strip(), pages


def _confidence_or_minus_one(result: OcrResult) -> float:
    return result.confidence if result.confidence is not None else -1.0


def _tesseract_with_gate(pdf_path: Path, derived: Path, settings: Settings) -> OcrResult:
    """Tesseract path; below the confidence threshold, retry via the photo path."""
    primary = tesseract.ocr_pdf(pdf_path, derived, languages=settings.ocr_languages)
    if primary.confidence is not None and primary.confidence >= settings.ocr_confidence_threshold:
        return primary
    retry = photo.ocr_pdf_pages(pdf_path)
    if _confidence_or_minus_one(retry) > _confidence_or_minus_one(primary):
        # Keep the Tesseract searchable PDF as the viewing artifact.
        return replace(retry, searchable_pdf=primary.searchable_pdf)
    return primary
