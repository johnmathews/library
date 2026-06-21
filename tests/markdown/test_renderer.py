import io
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

from library.markdown.renderer import render_page_images


def _make_pdf(path: Path, pages: int) -> None:
    pdf = pdfium.PdfDocument.new()
    for _ in range(pages):
        pdf.new_page(595, 842)  # A4 points
    pdf.save(str(path))


def test_renders_one_jpeg_per_pdf_page(tmp_path: Path) -> None:
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, 3)
    images = render_page_images("application/pdf", pdf, tmp_path, max_pages=20, long_side_px=1600)
    assert len(images) == 3
    for raw in images:
        img = Image.open(io.BytesIO(raw))
        assert img.format == "JPEG"
        assert max(img.size) <= 1600


def test_caps_at_max_pages(tmp_path: Path) -> None:
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, 5)
    images = render_page_images("application/pdf", pdf, tmp_path, max_pages=2, long_side_px=1600)
    assert len(images) == 2


def test_text_plain_returns_empty(tmp_path: Path) -> None:
    txt = tmp_path / "x"
    txt.write_text("hi")
    assert render_page_images("text/plain", txt, tmp_path, max_pages=20, long_side_px=1600) == []


def test_jpeg_image_single_page_downscaled(tmp_path: Path) -> None:
    src = tmp_path / "orig"
    Image.new("RGB", (4000, 2000), "white").save(src, format="JPEG")
    images = render_page_images("image/jpeg", src, tmp_path, max_pages=20, long_side_px=1600)
    assert len(images) == 1
    assert max(Image.open(io.BytesIO(images[0])).size) == 1600
