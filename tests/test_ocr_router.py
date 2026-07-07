"""Unit tests for the OCR router: branch selection and the confidence gate.

Engines are monkeypatched; routing logic, the text-layer detector, and the
scan-likeness analysis run for real on generated fixtures (the scan-like
fixture is a true full-page-image-plus-text-layer PDF, so no detection
mocking is needed). See tests/ocr_fixtures.py for the fixture strategy.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from library.config import Settings
from library.docx import CONVERTED_MARKDOWN_NAME, DOCX_MIME
from library.images import CONVERTED_JPEG_NAME
from library.models import Document, DocumentSource
from library.ocr import photo, router, tesseract
from library.ocr.base import OcrResult
from tests.docx_fixtures import make_docx
from tests.ocr_fixtures import make_image, make_image_pdf, make_scanlike_pdf, make_text_pdf

SHA = "0" * 64


def make_document(mime_type: str) -> Document:
    return Document(sha256=SHA, mime_type=mime_type, source=DocumentSource.UPLOAD)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        ocr_languages="nld+eng",
        ocr_confidence_threshold=65.0,
        text_layer_min_chars_per_page=50,
    )


@pytest.fixture
def derived(tmp_path: Path) -> Path:
    directory = tmp_path / "derived"
    directory.mkdir()
    return directory


def forbid(monkeypatch: pytest.MonkeyPatch, module: object, name: str) -> None:
    def _fail(*args: object, **kwargs: object) -> OcrResult:
        raise AssertionError(f"{name} must not be called on this route")

    monkeypatch.setattr(module, name, _fail)


def tesseract_result(searchable: Path, confidence: float | None) -> OcrResult:
    return OcrResult(
        text="tesseract text",
        confidence=confidence,
        searchable_pdf=searchable,
        engine="tesseract",
        pages=1,
    )


PHOTO_RESULT = OcrResult(
    text="photo text", confidence=80.0, searchable_pdf=None, engine="rapidocr", pages=1
)


class TestTextPassthrough:
    def test_txt_file_is_read_directly(
        self,
        tmp_path: Path,
        derived: Path,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        forbid(monkeypatch, tesseract, "ocr_pdf")
        forbid(monkeypatch, photo, "ocr_image")
        source = tmp_path / "note.txt"
        source.write_text("hello plain text", encoding="utf-8")

        result = router.run_ocr(make_document("text/plain"), source, derived, settings=settings)

        assert result.text == "hello plain text"
        assert result.engine == "text"
        assert result.confidence is None
        assert result.searchable_pdf is None
        assert result.pages is None

    def test_markdown_file_is_read_directly(
        self,
        tmp_path: Path,
        derived: Path,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        forbid(monkeypatch, tesseract, "ocr_pdf")
        forbid(monkeypatch, photo, "ocr_image")
        source = tmp_path / "note.md"
        source.write_text("# Heading\n\nbody text", encoding="utf-8")

        result = router.run_ocr(make_document("text/markdown"), source, derived, settings=settings)

        assert result.text == "# Heading\n\nbody text"
        assert result.engine == "text"
        assert result.confidence is None
        assert result.searchable_pdf is None
        assert result.pages is None

    def test_docx_reads_derived_markdown(
        self,
        tmp_path: Path,
        derived: Path,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # ingest wrote the Markdown conversion to the derived dir; the router
        # reads that cached artifact rather than touching the .docx original.
        forbid(monkeypatch, tesseract, "ocr_pdf")
        forbid(monkeypatch, photo, "ocr_image")
        (derived / CONVERTED_MARKDOWN_NAME).write_text("# Enrolment Form\n\nbody", encoding="utf-8")
        source = tmp_path / "form.docx"
        source.write_bytes(make_docx())

        result = router.run_ocr(make_document(DOCX_MIME), source, derived, settings=settings)

        assert result.text == "# Enrolment Form\n\nbody"
        assert result.engine == "docx"
        assert result.confidence is None
        assert result.searchable_pdf is None
        assert result.pages is None

    def test_docx_reconverts_original_when_derived_missing(
        self,
        tmp_path: Path,
        derived: Path,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # No cached converted.md (e.g. derived dir cleaned): the router falls back
        # to re-converting the stored .docx original.
        forbid(monkeypatch, tesseract, "ocr_pdf")
        forbid(monkeypatch, photo, "ocr_image")
        source = tmp_path / "form.docx"
        source.write_bytes(make_docx(heading="Enrolment Form"))

        result = router.run_ocr(make_document(DOCX_MIME), source, derived, settings=settings)

        assert result.engine == "docx"
        assert "# Enrolment Form" in result.text


class TestPdfRouting:
    def test_born_digital_pdf_skips_ocr(
        self,
        tmp_path: Path,
        derived: Path,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        forbid(monkeypatch, tesseract, "ocr_pdf")
        forbid(monkeypatch, photo, "ocr_pdf_pages")
        lines = ["Factuur 2026 - rekeningnummer NL12RABO0123456789."] * 5
        source = make_text_pdf(tmp_path / "digital.pdf", lines=lines, pages=2)

        result = router.run_ocr(
            make_document("application/pdf"), source, derived, settings=settings
        )

        assert result.engine == "text-layer"
        assert "Factuur 2026" in result.text
        assert result.pages == 2
        assert result.confidence is None
        assert result.searchable_pdf is None

    def test_image_only_pdf_routes_to_tesseract(
        self,
        tmp_path: Path,
        derived: Path,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        forbid(monkeypatch, photo, "ocr_pdf_pages")
        source = make_image_pdf(tmp_path / "scan.pdf")
        searchable = derived / "searchable.pdf"
        seen: list[tuple[Path, bool]] = []

        def fake_ocr_pdf(
            pdf_path: Path, derived_dir: Path, *, languages: str, redo: bool = False
        ) -> OcrResult:
            seen.append((pdf_path, redo))
            assert languages == "nld+eng"
            return tesseract_result(searchable, confidence=90.0)

        monkeypatch.setattr(tesseract, "ocr_pdf", fake_ocr_pdf)

        result = router.run_ocr(
            make_document("application/pdf"), source, derived, settings=settings
        )

        # No embedded text layer: plain (--skip-text) mode, not --redo-ocr.
        assert seen == [(source, False)]
        assert result.engine == "tesseract"
        assert result.text == "tesseract text"

    def test_scan_like_pdf_with_text_layer_routes_to_tesseract_redo(
        self,
        tmp_path: Path,
        derived: Path,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The W5 headline fix: an iOS-Notes-style scan export (full-page
        image + Apple's embedded OCR text) must be re-OCRed, not trusted."""
        forbid(monkeypatch, photo, "ocr_pdf_pages")
        source = make_scanlike_pdf(tmp_path / "notes-export.pdf")
        searchable = derived / "searchable.pdf"
        seen: list[tuple[Path, bool]] = []

        def fake_ocr_pdf(
            pdf_path: Path, derived_dir: Path, *, languages: str, redo: bool = False
        ) -> OcrResult:
            seen.append((pdf_path, redo))
            return tesseract_result(searchable, confidence=90.0)

        monkeypatch.setattr(tesseract, "ocr_pdf", fake_ocr_pdf)

        result = router.run_ocr(
            make_document("application/pdf"), source, derived, settings=settings
        )

        # Embedded text present -> --redo-ocr mode (--skip-text would skip every page).
        assert seen == [(source, True)]
        assert result.engine == "tesseract"

    def test_scan_like_tesseract_failure_falls_back_to_text_layer(
        self,
        tmp_path: Path,
        derived: Path,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """OCR failure on a scan with embedded text must not fail the
        document: the embedded layer is mediocre but better than nothing."""
        source = make_scanlike_pdf(tmp_path / "notes-export.pdf")

        def fail_ocr_pdf(*args: object, **kwargs: object) -> OcrResult:
            raise tesseract.TesseractError("ocrmypdf exited 1: boom")

        monkeypatch.setattr(tesseract, "ocr_pdf", fail_ocr_pdf)

        result = router.run_ocr(
            make_document("application/pdf"), source, derived, settings=settings
        )

        assert result.engine == "text-layer-fallback"
        assert "Factuur 2026" in result.text
        assert result.confidence is None
        assert result.searchable_pdf is None
        assert result.pages == 1

    def test_image_only_tesseract_failure_still_raises(
        self,
        tmp_path: Path,
        derived: Path,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No embedded text layer means there is nothing to fall back to."""
        source = make_image_pdf(tmp_path / "scan.pdf")

        def fail_ocr_pdf(*args: object, **kwargs: object) -> OcrResult:
            raise tesseract.TesseractError("ocrmypdf exited 1: boom")

        monkeypatch.setattr(tesseract, "ocr_pdf", fail_ocr_pdf)

        with pytest.raises(tesseract.TesseractError):
            router.run_ocr(make_document("application/pdf"), source, derived, settings=settings)

    def test_sparse_text_layer_still_goes_to_tesseract(
        self,
        tmp_path: Path,
        derived: Path,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # A 1-page PDF with fewer than 50 chars does not count as born-digital.
        source = make_text_pdf(tmp_path / "sparse.pdf", lines=["short"], pages=1)
        monkeypatch.setattr(
            tesseract,
            "ocr_pdf",
            lambda *a, **k: tesseract_result(derived / "searchable.pdf", 90.0),
        )

        result = router.run_ocr(
            make_document("application/pdf"), source, derived, settings=settings
        )

        assert result.engine == "tesseract"


class TestTiffRouting:
    def test_tiff_is_converted_to_pdf_then_tesseract(
        self,
        tmp_path: Path,
        derived: Path,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source = make_image(tmp_path / "scan.tiff")
        seen: list[Path] = []

        def fake_ocr_pdf(
            pdf_path: Path, derived_dir: Path, *, languages: str, redo: bool = False
        ) -> OcrResult:
            seen.append(pdf_path)
            assert redo is False  # a TIFF wrap never has an embedded text layer
            return tesseract_result(derived / "searchable.pdf", confidence=90.0)

        monkeypatch.setattr(tesseract, "ocr_pdf", fake_ocr_pdf)

        result = router.run_ocr(make_document("image/tiff"), source, derived, settings=settings)

        assert result.engine == "tesseract"
        assert len(seen) == 1
        assert seen[0].suffix == ".pdf"
        assert seen[0].parent == derived
        assert seen[0].exists()


class TestPhotoRouting:
    @pytest.mark.parametrize("mime", ["image/jpeg", "image/png"])
    def test_images_route_to_photo_engine(
        self,
        mime: str,
        tmp_path: Path,
        derived: Path,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        forbid(monkeypatch, tesseract, "ocr_pdf")
        source = tmp_path / "photo.bin"
        source.write_bytes(b"not really an image; engine is mocked")
        seen: list[Path] = []

        def fake_ocr_image(image_path: Path) -> OcrResult:
            seen.append(image_path)
            return PHOTO_RESULT

        monkeypatch.setattr(photo, "ocr_image", fake_ocr_image)

        result = router.run_ocr(make_document(mime), source, derived, settings=settings)

        assert seen == [source]
        assert result == PHOTO_RESULT

    @pytest.mark.parametrize("mime", ["image/heic", "image/heif"])
    def test_heic_uses_derived_converted_jpeg(
        self,
        mime: str,
        tmp_path: Path,
        derived: Path,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source = tmp_path / "original.heic"
        source.write_bytes(b"heic bytes (unused)")
        converted = derived / CONVERTED_JPEG_NAME
        converted.write_bytes(b"jpeg bytes (engine is mocked)")
        seen: list[Path] = []

        def fake_ocr_image(image_path: Path) -> OcrResult:
            seen.append(image_path)
            return PHOTO_RESULT

        monkeypatch.setattr(photo, "ocr_image", fake_ocr_image)

        result = router.run_ocr(make_document(mime), source, derived, settings=settings)

        assert seen == [converted]
        assert result == PHOTO_RESULT


class TestUnsupported:
    def test_unknown_mime_raises(self, tmp_path: Path, derived: Path, settings: Settings) -> None:
        source = tmp_path / "archive.zip"
        source.write_bytes(b"PK")
        with pytest.raises(router.UnsupportedOcrInputError):
            router.run_ocr(make_document("application/zip"), source, derived, settings=settings)


class TestConfidenceGate:
    """W5 gate rule: Tesseract word-conf and RapidOCR box-conf live on
    incomparable scales (the benchmark measured RapidOCR ~constant at 97-99
    while Tesseract spanned 83-95 on the same scans), so the retry is
    accepted on TEXT YIELD, never on cross-engine confidence: keep the retry
    only when it produced >= 0.8x Tesseract's character count."""

    @pytest.fixture
    def image_pdf(self, tmp_path: Path) -> Path:
        return make_image_pdf(tmp_path / "scan.pdf")

    def test_high_confidence_skips_retry(
        self,
        image_pdf: Path,
        derived: Path,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        searchable = derived / "searchable.pdf"
        monkeypatch.setattr(
            tesseract, "ocr_pdf", lambda *a, **k: tesseract_result(searchable, 80.0)
        )
        forbid(monkeypatch, photo, "ocr_pdf_pages")

        result = router.run_ocr(
            make_document("application/pdf"), image_pdf, derived, settings=settings
        )

        assert result.engine == "tesseract"
        assert result.confidence == 80.0
        assert result.gate is None

    def test_low_confidence_retry_with_comparable_text_is_kept(
        self,
        image_pdf: Path,
        derived: Path,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        searchable = derived / "searchable.pdf"
        monkeypatch.setattr(
            tesseract, "ocr_pdf", lambda *a, **k: tesseract_result(searchable, 40.0)
        )
        retried: list[Path] = []
        retry_text = "photo text of comparable length to the tesseract one"

        def fake_ocr_pdf_pages(pdf_path: Path) -> OcrResult:
            retried.append(pdf_path)
            return replace(PHOTO_RESULT, text=retry_text, confidence=98.0)

        monkeypatch.setattr(photo, "ocr_pdf_pages", fake_ocr_pdf_pages)

        result = router.run_ocr(
            make_document("application/pdf"), image_pdf, derived, settings=settings
        )

        assert retried == [image_pdf]
        assert result.engine == "rapidocr"
        assert result.text == retry_text
        # Confidence stays on the chosen engine's own scale; `engine` names it.
        assert result.confidence == 98.0
        # The Tesseract searchable-PDF artifact is kept as the viewing artifact.
        assert result.searchable_pdf == searchable
        # Both engines' confidences are recorded for the event detail.
        assert result.gate is not None
        assert result.gate.tesseract_confidence == 40.0
        assert result.gate.rapidocr_confidence == 98.0

    def test_low_confidence_retry_with_short_text_keeps_tesseract(
        self,
        image_pdf: Path,
        derived: Path,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A higher RapidOCR confidence must NOT win when it read less text:
        box confidence is near-constant ~98 regardless of actual quality."""
        searchable = derived / "searchable.pdf"
        monkeypatch.setattr(
            tesseract, "ocr_pdf", lambda *a, **k: tesseract_result(searchable, 40.0)
        )
        # 10 chars < 0.8 x 14 chars ("tesseract text") -> retry rejected.
        monkeypatch.setattr(
            photo,
            "ocr_pdf_pages",
            lambda pdf_path: replace(PHOTO_RESULT, text="photo text", confidence=98.0),
        )

        result = router.run_ocr(
            make_document("application/pdf"), image_pdf, derived, settings=settings
        )

        assert result.engine == "tesseract"
        assert result.text == "tesseract text"
        assert result.confidence == 40.0
        assert result.gate is not None
        assert result.gate.tesseract_confidence == 40.0
        assert result.gate.rapidocr_confidence == 98.0

    def test_retry_text_exactly_at_ratio_is_kept(
        self,
        image_pdf: Path,
        derived: Path,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        searchable = derived / "searchable.pdf"
        primary_text = "x" * 100
        monkeypatch.setattr(
            tesseract,
            "ocr_pdf",
            lambda *a, **k: replace(tesseract_result(searchable, 40.0), text=primary_text),
        )
        monkeypatch.setattr(
            photo,
            "ocr_pdf_pages",
            lambda pdf_path: replace(PHOTO_RESULT, text="y" * 80, confidence=98.0),
        )

        result = router.run_ocr(
            make_document("application/pdf"), image_pdf, derived, settings=settings
        )

        assert result.engine == "rapidocr"  # 80 >= 0.8 * 100

    def test_no_confidence_at_all_triggers_retry(
        self,
        image_pdf: Path,
        derived: Path,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        searchable = derived / "searchable.pdf"
        monkeypatch.setattr(
            tesseract, "ocr_pdf", lambda *a, **k: tesseract_result(searchable, None)
        )
        retry_text = "photo text of comparable length to the tesseract one"
        monkeypatch.setattr(
            photo,
            "ocr_pdf_pages",
            lambda pdf_path: replace(PHOTO_RESULT, text=retry_text, confidence=55.0),
        )

        result = router.run_ocr(
            make_document("application/pdf"), image_pdf, derived, settings=settings
        )

        assert result.engine == "rapidocr"
        assert result.confidence == 55.0
        assert result.gate is not None
        assert result.gate.tesseract_confidence is None
