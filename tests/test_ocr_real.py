"""Real-engine OCR tests, marked ``slow_ocr``.

These run the actual binaries/models and are required to pass in CI (the
backend job installs tesseract-ocr{,-nld,-eng}, ghostscript, and unpaper).
Only the Tesseract path skips locally, and only for a missing binary:

- Tesseract path: needs ``tesseract`` + ``gs`` + ``unpaper`` on PATH (our
  fixed OCRmyPDF flag set uses --clean and PDF/A output). If the ``nld``
  language pack is absent the test falls back to ``eng`` — the language is a
  setting, not pipeline logic.
- RapidOCR path: never skips. Its weights are vendored in ``models/ocr/``
  (GH #109), so a checkout is all it needs — see ``require_rapidocr_engine``.

The RapidOCR guard used to skip when the model hub was unreachable, because
the weights were fetched on first use and that fetch was genuinely out of our
control. Vendoring removed the only legitimate trigger, so the skip went with
it: a skip with no reachable cause is an invisible pass waiting to happen.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from library.ocr import photo, tesseract, weights
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
    """Build the shared RapidOCR engine. Nothing here may skip.

    The name is now a slight misnomer — it requires rather than skips — but it
    is kept because what it guards is unchanged: this and the Tesseract test
    are the only coverage the OCR pipeline has over the real engines.

    It used to skip on ``OSError``/``DownloadFileException``, because the
    PP-OCRv5 weights were fetched from ``modelscope.cn`` on first construction
    and a hub outage was not our bug. Since GH #109 the weights are committed
    under ``models/ocr/`` and loaded from disk, so that trigger cannot fire in
    any environment that has this repository checked out — and a skip branch
    with no reachable cause is exactly the invisible pass that
    ``scripts/check_engine_skips.py`` exists to prevent. Every failure is now
    ours, and every failure now fails:

    - a weight absent from ``MODEL_DIR`` means the vendored file was lost or
      never fetched, which is a broken checkout, not a broken network;
    - ``ImportError``/``AttributeError``/``TypeError``/``ValueError`` mean a
      rapidocr release moved something under us (the 3.9.2 bump did exactly
      that to ``Det.model_type``);
    - ``OSError`` no longer has a legitimate source at all, since nothing is
      downloaded — a disk or permissions problem is worth failing on.

    Checked before construction rather than left to rapidocr, because rapidocr
    responds to a missing weight by trying to download it: without this the
    test would hang on a dead host and then report a network error for what is
    really a missing file.
    """
    missing = weights.missing_models()
    if missing:
        names = ", ".join(str(model.path) for model in missing)
        pytest.fail(
            f"vendored RapidOCR weights are missing ({names}). They are committed "
            "to this repository; restore them with "
            "`python -m scripts.fetch_ocr_models`."
        )
    photo.get_engine()


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
    """The guard's contract: nothing skips. Every failure fails.

    These do not touch a real engine — they monkeypatch ``photo.get_engine``
    and drive ``require_rapidocr_engine`` directly. They assert with explicit
    try/except rather than by actually skipping, so they always report
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
            # The two that USED to skip. Since the weights are vendored,
            # neither has a legitimate cause left, so both must now surface.
            # Parametrized here rather than deleted: these are the exact
            # exception types the old skip branch caught, and the point of the
            # change is that they no longer buy silence.
            pytest.param(OSError("connection refused"), id="oserror"),
        ],
    )
    def test_every_failure_fails_rather_than_skips(
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

    def test_download_failure_fails_rather_than_skips(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """rapidocr's own download error no longer buys a skip.

        Not folded into the parametrize above: importing the exception is the
        assertion that it still exists where we expect, and a rapidocr release
        that moves it should fail here rather than quietly reduce the case list.
        """
        from rapidocr.utils.download_file import DownloadFileException

        def hub_down() -> None:
            raise DownloadFileException("Failed to download https://example.invalid/model.onnx")

        monkeypatch.setattr(photo, "get_engine", hub_down)

        try:
            require_rapidocr_engine()
        except pytest.skip.Exception as skipped:
            pytest.fail(f"guard swallowed a download failure as a skip: {skipped}")
        except DownloadFileException as propagated:
            assert "Failed to download" in str(propagated)
        else:
            pytest.fail("guard neither propagated nor skipped a download failure")

    def test_missing_vendored_weights_fail_with_a_recovery_hint(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A lost weight file fails before construction, naming the fix.

        Checked ahead of ``get_engine`` on purpose: rapidocr answers a missing
        weight by downloading it, so without this the symptom of a broken
        checkout would be a network error against modelscope — the very
        confusion GH #109 was about.
        """

        def must_not_run() -> None:  # pragma: no cover - the assertion is that it does not
            pytest.fail("guard built the engine despite missing weights")

        monkeypatch.setattr(photo, "get_engine", must_not_run)
        monkeypatch.setattr(weights, "MODEL_DIR", tmp_path)
        weights.pinned_models.cache_clear()
        try:
            with pytest.raises(pytest.fail.Exception) as failure:
                require_rapidocr_engine()
        finally:
            weights.pinned_models.cache_clear()
        assert "fetch_ocr_models" in str(failure.value)
