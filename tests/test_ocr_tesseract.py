"""Unit tests for the Tesseract engine helpers (no tesseract binary needed)."""

from pathlib import Path

import pypdfium2 as pdfium
import pytest

from library.ocr import tesseract
from tests.ocr_fixtures import make_image

# A realistic tesseract TSV snippet: header, page/block/line rows (conf -1),
# real words, a conf-0 word, and a whitespace "word" that must be ignored.
TSV = "\n".join(
    [
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
        "1\t1\t0\t0\t0\t0\t0\t0\t100\t100\t-1\t",
        "4\t1\t1\t1\t1\t0\t10\t10\t80\t20\t-1\t",
        "5\t1\t1\t1\t1\t1\t10\t10\t30\t20\t96.5\tFactuur",
        "5\t1\t1\t1\t1\t2\t50\t10\t30\t20\t83.5\t2026",
        "5\t1\t1\t1\t1\t3\t90\t10\t10\t20\t0\trekening",
        "5\t1\t1\t1\t1\t4\t90\t10\t10\t20\t55\t ",
        "",
    ]
)


class TestParseTsvConfidences:
    def test_word_rows_only(self) -> None:
        assert tesseract.parse_tsv_confidences(TSV) == [96.5, 83.5, 0.0]

    def test_empty_input(self) -> None:
        assert tesseract.parse_tsv_confidences("") == []

    def test_header_only(self) -> None:
        header = TSV.splitlines()[0]
        assert tesseract.parse_tsv_confidences(header) == []


class TestTiffToPdf:
    def test_single_page_tiff(self, tmp_path: Path) -> None:
        source = make_image(tmp_path / "scan.tiff")
        derived = tmp_path / "derived"
        derived.mkdir()

        pdf_path = tesseract.tiff_to_pdf(source, derived)

        assert pdf_path.parent == derived
        document = pdfium.PdfDocument(str(pdf_path))
        try:
            assert len(document) == 1
        finally:
            document.close()

    def test_multi_frame_tiff(self, tmp_path: Path) -> None:
        from PIL import Image

        frames = [Image.new("L", (200, 100), color=value) for value in (255, 200, 150)]
        source = tmp_path / "multi.tiff"
        frames[0].save(source, save_all=True, append_images=frames[1:])
        derived = tmp_path / "derived"
        derived.mkdir()

        pdf_path = tesseract.tiff_to_pdf(source, derived)

        document = pdfium.PdfDocument(str(pdf_path))
        try:
            assert len(document) == 3
        finally:
            document.close()


class TestOcrPdfFailure:
    def test_nonzero_exit_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import subprocess

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args=[], returncode=2, stdout="", stderr="boom")

        monkeypatch.setattr(subprocess, "run", fake_run)
        source = tmp_path / "in.pdf"
        source.write_bytes(b"%PDF-1.4")
        with pytest.raises(tesseract.TesseractError, match="boom"):
            tesseract.ocr_pdf(source, tmp_path, languages="eng")


class TestOcrPdfFlagSets:
    """ocrmypdf 17.x rejects --redo-ocr combined with --deskew, --clean-final
    or --remove-background (its documented conflict set; plain --clean still
    applies to the OCR input image), so the redo flag set must drop --deskew
    while the default (--skip-text) set keeps it."""

    def _captured_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, redo: bool
    ) -> list[str]:
        import subprocess

        commands: list[list[str]] = []
        sidecar = tmp_path / "ocr.txt"

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            sidecar.write_text("ocr text", encoding="utf-8")
            return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(tesseract, "pdf_page_count", lambda path: 1)
        monkeypatch.setattr(tesseract, "mean_word_confidence", lambda path, **kwargs: 90.0)
        source = tmp_path / "in.pdf"
        source.write_bytes(b"%PDF-1.4")

        result = tesseract.ocr_pdf(source, tmp_path, languages="eng", redo=redo)
        assert result.text == "ocr text"
        assert len(commands) == 1
        return commands[0]

    def test_default_mode_uses_skip_text_and_deskew(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        command = self._captured_command(tmp_path, monkeypatch, redo=False)
        assert "--skip-text" in command
        assert "--deskew" in command
        assert "--clean" in command
        assert "--redo-ocr" not in command

    def test_redo_mode_drops_deskew_and_skip_text(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        command = self._captured_command(tmp_path, monkeypatch, redo=True)
        assert "--redo-ocr" in command
        assert "--skip-text" not in command
        assert "--deskew" not in command  # incompatible with --redo-ocr
        assert "--clean" in command  # input-image clean is still allowed
        assert "--clean-final" not in command
        assert "--remove-background" not in command
