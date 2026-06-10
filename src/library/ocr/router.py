"""OCR router: pick the right engine for a document and apply the gate.

Routing (see docs/ingestion.md):

- ``text/plain``                  -> passthrough read (engine "text")
- ``application/pdf`` w/ text layer, NOT scan-like
                                  -> pypdfium2 extraction (engine "text-layer")
- ``application/pdf`` scan-like (with or without embedded text)
                                  -> Tesseract path + confidence gate
                                     (``--redo-ocr`` when embedded text exists);
                                     on Tesseract FAILURE with a usable embedded
                                     text layer, fall back to it
                                     (engine "text-layer-fallback")
- ``application/pdf`` image-only/sparse -> Tesseract path + confidence gate
- ``image/tiff``                  -> wrapped into a PDF, then Tesseract path
- ``image/jpeg``/``image/png``    -> photo path (OpenCV + RapidOCR)
- ``image/heic``/``image/heif``   -> photo path on the derived converted.jpg

Scan-likeness matters because iOS Notes scan exports (the primary input
type, per the W5 benchmark) embed Apple's own mediocre OCR text — a router
that trusts any text layer would never OCR them.

Confidence gate: a Tesseract result below ``ocr_confidence_threshold`` (or
with no measurable confidence) is retried through the photo path on the
rasterized pages. Tesseract word confidence and RapidOCR box confidence are
not comparable (W5 measured RapidOCR near-constant at 97-99), so the retry
wins on TEXT YIELD only: it is kept iff it produced at least
``RETRY_MIN_TEXT_RATIO`` x Tesseract's character count. The Tesseract
``searchable.pdf`` artifact is kept as the viewing artifact either way, and
both confidences are recorded in ``OcrResult.gate``.
"""

from dataclasses import replace
from pathlib import Path

from library.config import Settings, get_settings
from library.images import CONVERTED_JPEG_NAME, HEIC_MIME_TYPES
from library.models import Document
from library.ocr import photo, tesseract
from library.ocr.analysis import PdfAnalysis, analyze_pdf
from library.ocr.base import GateRetry, OcrResult

PHOTO_MIME_TYPES: frozenset[str] = frozenset({"image/jpeg", "image/png"})
# Gate-retry acceptance: the photo path must read at least this fraction of
# the characters Tesseract read, or its result is discarded.
RETRY_MIN_TEXT_RATIO: float = 0.8


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
        return _route_pdf(original_path, derived, settings)

    if mime_type == "image/tiff":
        pdf_path = tesseract.tiff_to_pdf(original_path, derived)
        return _tesseract_with_gate(pdf_path, derived, settings)

    if mime_type in HEIC_MIME_TYPES:
        return photo.ocr_image(derived / CONVERTED_JPEG_NAME)

    if mime_type in PHOTO_MIME_TYPES:
        return photo.ocr_image(original_path)

    raise UnsupportedOcrInputError(mime_type)


def _route_pdf(pdf_path: Path, derived: Path, settings: Settings) -> OcrResult:
    """Scan-aware PDF routing (see module docstring)."""
    analysis = analyze_pdf(pdf_path)
    has_text_layer = (
        analysis.pages > 0 and analysis.chars_per_page >= settings.text_layer_min_chars_per_page
    )
    if has_text_layer and not analysis.scan_like:
        # Born-digital: the text layer is authoritative; no OCR.
        return OcrResult(
            text=analysis.text,
            confidence=None,
            searchable_pdf=None,
            engine="text-layer",
            pages=analysis.pages,
        )
    # Scan-like (the embedded text, if any, is scan-app OCR to be redone) or
    # too little text to trust. ``--redo-ocr`` whenever ANY embedded text
    # exists: ``--skip-text`` would skip every page that has text on it.
    try:
        return _tesseract_with_gate(pdf_path, derived, settings, redo=bool(analysis.text))
    except tesseract.TesseractError:
        if has_text_layer:
            # Mediocre embedded OCR beats failing the whole document.
            return _text_layer_fallback(analysis)
        raise


def _text_layer_fallback(analysis: PdfAnalysis) -> OcrResult:
    return OcrResult(
        text=analysis.text,
        confidence=None,
        searchable_pdf=None,
        engine="text-layer-fallback",
        pages=analysis.pages,
    )


def _tesseract_with_gate(
    pdf_path: Path, derived: Path, settings: Settings, *, redo: bool = False
) -> OcrResult:
    """Tesseract path; below the confidence threshold, retry via the photo path."""
    primary = tesseract.ocr_pdf(pdf_path, derived, languages=settings.ocr_languages, redo=redo)
    if primary.confidence is not None and primary.confidence >= settings.ocr_confidence_threshold:
        return primary
    retry = photo.ocr_pdf_pages(pdf_path)
    gate = GateRetry(tesseract_confidence=primary.confidence, rapidocr_confidence=retry.confidence)
    if len(retry.text) >= RETRY_MIN_TEXT_RATIO * len(primary.text):
        # Keep the Tesseract searchable PDF as the viewing artifact.
        return replace(retry, searchable_pdf=primary.searchable_pdf, gate=gate)
    return replace(primary, gate=gate)
