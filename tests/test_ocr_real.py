"""Real-engine OCR tests, marked ``slow_ocr``.

These run the actual binaries/models and are required to pass in CI (the
backend job installs tesseract-ocr{,-nld,-eng}, ghostscript, and unpaper).
Locally they skip gracefully when a dependency is missing:

- Tesseract path: needs ``tesseract`` + ``gs`` + ``unpaper`` on PATH (our
  fixed OCRmyPDF flag set uses --clean and PDF/A output). If the ``nld``
  language pack is absent the test falls back to ``eng`` — the language is a
  setting, not pipeline logic.
- RapidOCR path: downloads PP-OCRv5 models on first use; if engine
  initialisation fails (no network / blocked model hub) the test skips with
  the underlying error rather than failing CI on flaky downloads.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from library.ocr import photo, tesseract
from tests.ocr_fixtures import make_image, make_image_pdf

pytestmark = pytest.mark.slow_ocr

TEXT = "Factuur 2026 rekening 12345"


def tesseract_languages() -> set[str]:
    proc = subprocess.run(
        ["tesseract", "--list-langs"], capture_output=True, text=True, check=False
    )
    return {line.strip() for line in proc.stdout.splitlines()[1:] if line.strip()}


def require_tesseract_stack() -> str:
    """Skip unless the full OCRmyPDF binary stack is present; return languages."""
    for binary, reason in (
        ("tesseract", "tesseract binary not installed"),
        ("gs", "ghostscript not installed (OCRmyPDF PDF/A output)"),
        ("unpaper", "unpaper not installed (OCRmyPDF --clean)"),
    ):
        if shutil.which(binary) is None:
            pytest.skip(reason)
    available = tesseract_languages()
    if {"nld", "eng"} <= available:
        return "nld+eng"
    if "eng" in available:
        return "eng"
    pytest.skip("no usable tesseract language packs (need eng at minimum)")


class TestRealTesseract:
    def test_image_pdf_roundtrip(self, tmp_path: Path) -> None:
        languages = require_tesseract_stack()
        source = make_image_pdf(tmp_path / "scan.pdf", text=TEXT)
        derived = tmp_path / "derived"
        derived.mkdir()

        result = tesseract.ocr_pdf(source, derived, languages=languages)

        lowered = result.text.lower()
        assert "factuur" in lowered
        assert "2026" in lowered
        assert "rekening" in lowered
        assert result.engine == "tesseract"
        assert result.pages == 1
        assert result.searchable_pdf is not None
        assert result.searchable_pdf.exists()
        assert result.searchable_pdf.parent == derived
        assert result.confidence is not None
        assert 0.0 < result.confidence <= 100.0
        # The sidecar text artifact is persisted alongside the searchable PDF.
        assert (derived / tesseract.SIDECAR_NAME).exists()


class TestRealRapidOcr:
    def test_photo_roundtrip(self, tmp_path: Path) -> None:
        try:
            photo.get_engine()
        except Exception as exc:  # model download / init failure -> skip
            pytest.skip(f"rapidocr engine unavailable: {exc}")
        source = make_image(tmp_path / "photo.jpg", text=TEXT)

        result = photo.ocr_image(source)

        lowered = result.text.lower()
        assert "factuur" in lowered
        assert "rekening" in lowered
        assert result.engine == "rapidocr"
        assert result.searchable_pdf is None
        assert result.pages == 1
        assert result.confidence is not None
        assert 0.0 < result.confidence <= 100.0
