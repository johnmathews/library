"""Real-engine OCR tests, marked ``slow_ocr``.

These run the actual binaries/models and are required to pass in CI (the
backend job installs tesseract-ocr{,-nld,-eng}, ghostscript, and unpaper).
Locally they skip gracefully when a dependency is missing:

- Tesseract path: needs ``tesseract`` + ``gs`` + ``unpaper`` on PATH (our
  fixed OCRmyPDF flag set uses --clean and PDF/A output). If the ``nld``
  language pack is absent the test falls back to ``eng`` — the language is a
  setting, not pipeline logic.
- RapidOCR path: downloads PP-OCRv5 models on first use, so a host with no
  network or a blocked model hub skips with the underlying error rather than
  failing on a flaky download. That skip is deliberately narrow — see
  ``require_rapidocr_engine`` — and CI additionally asserts that it never
  fires, so a permanently broken engine cannot hide behind it.
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


def require_rapidocr_engine() -> None:
    """Build the shared RapidOCR engine; skip only on environmental failure.

    Narrow on purpose. The only thing about this test that is legitimately out
    of our control is fetching the PP-OCRv5 weights from the model hub, so that
    — and nothing else — is what may skip:

    - ``OSError`` covers the socket/DNS/TLS/filesystem layer, and by subclass
      ``TimeoutError`` and ``requests``' own errors.
    - ``DownloadFileException`` is rapidocr's download wrapper, which is a bare
      ``Exception`` subclass and so needs naming explicitly.

    Everything else propagates and fails the test, because everything else is
    our bug: ``ImportError`` for a removed export, ``AttributeError`` for a
    renamed enum member, ``TypeError`` for a changed constructor signature and
    ``ValueError`` for a params dict rapidocr no longer accepts. A bare
    ``except Exception`` here — which is what this replaced — reported all four
    as "engine unavailable" and skipped, so a total breakage of the photo OCR
    path looked exactly like a flaky download.

    That is not a hypothetical: the rapidocr 3.9.2 bump landed with this guard
    narrowed, and the ``ValueError`` from a params dict whose ``Det.model_type``
    default had shifted under us surfaced immediately instead of turning CI
    green over a pipeline that raised on every photo.
    """
    # Not re-exported at package level, so this reaches into the defining
    # module. A release that moves it raises ImportError here and the test
    # fails loudly — the intended direction for a rapidocr-side change.
    from rapidocr.utils.download_file import DownloadFileException

    try:
        photo.get_engine()
    except (OSError, DownloadFileException) as exc:
        pytest.skip(f"rapidocr engine unavailable: {exc}")


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
        require_rapidocr_engine()
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


class TestRapidOcrGuard:
    """The guard's contract: environmental failures skip, our bugs do not.

    These do not touch a real engine — they monkeypatch ``photo.get_engine``
    and drive ``require_rapidocr_engine`` directly. They assert with
    ``pytest.raises`` rather than by actually skipping, so they always report
    pass/fail and never contribute a skip of their own to the CI floor.
    """

    @pytest.mark.parametrize(
        "error",
        [
            pytest.param(TypeError("unexpected keyword argument 'params'"), id="typeerror"),
            pytest.param(ValueError("Invalid OCR configuration."), id="valueerror"),
            pytest.param(AttributeError("PPOCRV5"), id="attributeerror"),
            pytest.param(ImportError("cannot import name 'LangRec'"), id="importerror"),
            pytest.param(RuntimeError("onnxruntime ABI mismatch"), id="runtimeerror"),
        ],
    )
    def test_our_bugs_fail_rather_than_skip(
        self, monkeypatch: pytest.MonkeyPatch, error: Exception
    ) -> None:
        def broken() -> None:
            raise error

        monkeypatch.setattr(photo, "get_engine", broken)

        # Deliberately not pytest.raises(type(error)): a Skipped raised by the
        # guard would propagate straight through pytest.raises and mark THIS
        # test skipped, so the regression would be invisible in the one test
        # written to catch it. Catch Skipped first and convert it to a failure.
        try:
            require_rapidocr_engine()
        except pytest.skip.Exception as skipped:
            pytest.fail(f"guard swallowed {error!r} as a skip: {skipped}")
        except type(error) as propagated:
            assert propagated is error
        else:
            pytest.fail(f"guard neither propagated nor skipped on {error!r}")

    def test_network_failure_skips_with_underlying_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def offline() -> None:
            raise OSError("connection refused")

        monkeypatch.setattr(photo, "get_engine", offline)

        with pytest.raises(pytest.skip.Exception) as skipped:
            require_rapidocr_engine()
        assert "connection refused" in str(skipped.value)

    def test_download_failure_skips_with_underlying_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from rapidocr.utils.download_file import DownloadFileException

        def hub_down() -> None:
            raise DownloadFileException("Failed to download https://example.invalid/model.onnx")

        monkeypatch.setattr(photo, "get_engine", hub_down)

        with pytest.raises(pytest.skip.Exception) as skipped:
            require_rapidocr_engine()
        assert "Failed to download" in str(skipped.value)
