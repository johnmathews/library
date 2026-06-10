"""Unit tests for scan detection (library.ocr.analysis).

The W5 benchmark showed every iOS Notes scan export carries an Apple-OCR
text layer, so routing cannot trust "has a text layer" alone: it must know
whether the pages are really raster scans. These tests pin the detection
contract on generated fixtures (see tests/ocr_fixtures.py).
"""

from pathlib import Path

from library.ocr.analysis import PdfAnalysis, analyze_pdf
from tests.ocr_fixtures import make_image_pdf, make_scanlike_pdf, make_text_pdf


class TestAnalyzePdf:
    def test_born_digital_pdf_has_no_image_backed_pages(self, tmp_path: Path) -> None:
        lines = ["Factuur 2026 - rekeningnummer NL12RABO0123456789."] * 5
        pdf = make_text_pdf(tmp_path / "digital.pdf", lines=lines, pages=2)

        analysis = analyze_pdf(pdf)

        assert analysis.pages == 2
        assert analysis.image_backed_pages == 0
        assert not analysis.scan_like
        assert "Factuur 2026" in analysis.text
        assert analysis.chars_per_page > 50

    def test_image_only_pdf_is_scan_like_without_text(self, tmp_path: Path) -> None:
        pdf = make_image_pdf(tmp_path / "scan.pdf")

        analysis = analyze_pdf(pdf)

        assert analysis.pages == 1
        assert analysis.image_backed_pages == 1
        assert analysis.scan_like
        assert analysis.text == ""
        assert analysis.chars_per_page == 0.0

    def test_scan_with_embedded_text_layer_is_scan_like(self, tmp_path: Path) -> None:
        """The iOS Notes shape: full-page image AND an extractable text layer."""
        pdf = make_scanlike_pdf(tmp_path / "notes-export.pdf", pages=2)

        analysis = analyze_pdf(pdf)

        assert analysis.pages == 2
        assert analysis.image_backed_pages == 2
        assert analysis.scan_like
        assert "Factuur 2026" in analysis.text
        assert analysis.chars_per_page > 50

    def test_mostly_text_pages_with_one_scan_page_is_not_scan_like(self, tmp_path: Path) -> None:
        """A born-digital report with one embedded scan page must keep its
        text-layer route (benchmark sample-05: 4 image-backed of 28 pages)."""
        pdf = make_scanlike_pdf(tmp_path / "mixed.pdf", pages=4, image_pages=1)

        analysis = analyze_pdf(pdf)

        assert analysis.pages == 4
        assert analysis.image_backed_pages == 1
        assert not analysis.scan_like

    def test_majority_image_backed_pages_is_scan_like(self, tmp_path: Path) -> None:
        pdf = make_scanlike_pdf(tmp_path / "mostly-scan.pdf", pages=4, image_pages=2)

        analysis = analyze_pdf(pdf)

        assert analysis.image_backed_pages == 2
        assert analysis.scan_like  # 2/4 meets the >= 50% page fraction


class TestPdfAnalysisProperties:
    def test_empty_document_edge_cases(self) -> None:
        analysis = PdfAnalysis(page_chars=(), image_backed_pages=0, text="")
        assert analysis.pages == 0
        assert analysis.chars_per_page == 0.0
        assert not analysis.scan_like

    def test_chars_per_page_uses_page_count(self) -> None:
        analysis = PdfAnalysis(page_chars=(100, 200), image_backed_pages=0, text="x" * 300)
        assert analysis.pages == 2
        assert analysis.chars_per_page == 150.0
