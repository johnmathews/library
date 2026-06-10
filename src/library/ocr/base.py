"""OCR engine contract: the result every engine returns and the call shape."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class OcrResult:
    """Outcome of running OCR (or text extraction) on one document.

    ``confidence`` is on a 0-100 scale (Tesseract's native word-confidence
    scale; RapidOCR's 0-1 scores are multiplied by 100 so the confidence
    gate compares like with like) or ``None`` when the engine has no
    confidence notion (text layer, plain text) or found no words.
    """

    text: str
    confidence: float | None
    searchable_pdf: Path | None
    engine: str
    pages: int | None


@runtime_checkable
class OcrEngine(Protocol):
    """Call shape for OCR engines (documentation/typing aid for future engines)."""

    name: str

    def run(self, source: Path, derived: Path) -> OcrResult:
        """OCR ``source``, writing any artifacts into ``derived``."""
        ...
