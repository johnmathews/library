"""OCR engine contract: the result every engine returns and the call shape."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class GateRetry:
    """Both engines' confidences when the confidence gate triggered a retry.

    Tesseract word confidence and RapidOCR box confidence are NOT comparable
    (the W5 benchmark measured RapidOCR near-constant at 97-99 while
    Tesseract spanned 83-95 on the same scans), so both raw values are
    recorded for the audit trail instead of being compared against each
    other. The kept engine is named by ``OcrResult.engine``.
    """

    tesseract_confidence: float | None
    rapidocr_confidence: float | None


@dataclass(frozen=True, slots=True)
class OcrResult:
    """Outcome of running OCR (or text extraction) on one document.

    ``confidence`` is on the producing engine's own 0-100 scale (Tesseract
    mean word confidence, or RapidOCR mean box score x100); ``engine`` names
    which scale applies. It is ``None`` when the engine has no confidence
    notion (text layer, plain text) or found no words. ``gate`` is set only
    when the confidence gate ran a photo-path retry, and carries both
    engines' raw confidences.
    """

    text: str
    confidence: float | None
    searchable_pdf: Path | None
    engine: str
    pages: int | None
    gate: GateRetry | None = None


@runtime_checkable
class OcrEngine(Protocol):
    """Call shape for OCR engines (documentation/typing aid for future engines)."""

    name: str

    def run(self, source: Path, derived: Path) -> OcrResult:
        """OCR ``source``, writing any artifacts into ``derived``."""
        ...
