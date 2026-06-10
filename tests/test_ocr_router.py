"""Unit tests for the OCR router: branch selection and the confidence gate.

Engines are monkeypatched; only routing logic and the text-layer detector run
for real. See tests/ocr_fixtures.py for the fixture strategy.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from library.config import Settings
from library.images import CONVERTED_JPEG_NAME
from library.models import Document, DocumentSource
from library.ocr import photo, router, tesseract
from library.ocr.base import OcrResult
from tests.ocr_fixtures import make_image, make_image_pdf, make_text_pdf

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
        seen: list[Path] = []

        def fake_ocr_pdf(pdf_path: Path, derived_dir: Path, *, languages: str) -> OcrResult:
            seen.append(pdf_path)
            assert languages == "nld+eng"
            return tesseract_result(searchable, confidence=90.0)

        monkeypatch.setattr(tesseract, "ocr_pdf", fake_ocr_pdf)

        result = router.run_ocr(
            make_document("application/pdf"), source, derived, settings=settings
        )

        assert seen == [source]
        assert result.engine == "tesseract"
        assert result.text == "tesseract text"

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

        def fake_ocr_pdf(pdf_path: Path, derived_dir: Path, *, languages: str) -> OcrResult:
            seen.append(pdf_path)
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

    def test_low_confidence_retries_and_keeps_better_photo_result(
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

        def fake_ocr_pdf_pages(pdf_path: Path) -> OcrResult:
            retried.append(pdf_path)
            return replace(PHOTO_RESULT, confidence=75.0)

        monkeypatch.setattr(photo, "ocr_pdf_pages", fake_ocr_pdf_pages)

        result = router.run_ocr(
            make_document("application/pdf"), image_pdf, derived, settings=settings
        )

        assert retried == [image_pdf]
        assert result.engine == "rapidocr"
        assert result.text == "photo text"
        assert result.confidence == 75.0
        # The Tesseract searchable-PDF artifact is kept as the viewing artifact.
        assert result.searchable_pdf == searchable

    def test_low_confidence_retry_keeps_tesseract_when_photo_is_worse(
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
        monkeypatch.setattr(
            photo, "ocr_pdf_pages", lambda pdf_path: replace(PHOTO_RESULT, confidence=20.0)
        )

        result = router.run_ocr(
            make_document("application/pdf"), image_pdf, derived, settings=settings
        )

        assert result.engine == "tesseract"
        assert result.confidence == 40.0

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
        monkeypatch.setattr(
            photo, "ocr_pdf_pages", lambda pdf_path: replace(PHOTO_RESULT, confidence=55.0)
        )

        result = router.run_ocr(
            make_document("application/pdf"), image_pdf, derived, settings=settings
        )

        assert result.engine == "rapidocr"
        assert result.confidence == 55.0
