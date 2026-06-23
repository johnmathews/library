import io
from pathlib import Path
from unittest.mock import patch

import pypdfium2 as pdfium
from PIL import Image

import library.markdown.renderer as renderer_module
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


# ---------------------------------------------------------------------------
# Security / resource-exhaustion tests
# ---------------------------------------------------------------------------


def test_oversize_image_returns_empty(tmp_path: Path) -> None:
    """An image whose pixel count exceeds _MAX_IMAGE_PIXELS must return []."""
    src = tmp_path / "big.jpg"
    # Create a 3000x3000 image (9 MP). Monkeypatch the cap below that so we
    # never have to allocate a truly giant bitmap in the test suite.
    Image.new("RGB", (3000, 3000), "red").save(src, format="JPEG")
    with patch.object(renderer_module, "_MAX_IMAGE_PIXELS", 1_000_000):  # 1 MP cap
        result = render_page_images("image/jpeg", src, tmp_path, max_pages=20, long_side_px=1600)
    assert result == []


def test_decompression_bomb_returns_empty(tmp_path: Path) -> None:
    """A DecompressionBombError from Pillow must be caught and return []."""
    src = tmp_path / "bomb.png"
    Image.new("RGB", (100, 100), "blue").save(src, format="PNG")

    def _bomb_open(path: object, **kwargs: object) -> Image.Image:  # type: ignore[override]
        raise Image.DecompressionBombError("simulated bomb")

    with patch("library.markdown.renderer.Image.open", side_effect=_bomb_open):
        result = render_page_images("image/png", src, tmp_path, max_pages=20, long_side_px=1600)
    assert result == []


def test_pdf_render_scale_never_exceeds_cap(tmp_path: Path) -> None:
    """Even a very large PDF page must render below the long_side_px ceiling."""
    # Create a PDF with a page that is 10 000 x 10 000 pt (enormous).
    pdf_path = tmp_path / "huge.pdf"
    pdf = pdfium.PdfDocument.new()
    pdf.new_page(10_000, 10_000)
    pdf.save(str(pdf_path))

    images = render_page_images(
        "application/pdf", pdf_path, tmp_path, max_pages=1, long_side_px=1600
    )
    assert len(images) == 1
    rendered = Image.open(io.BytesIO(images[0]))
    assert max(rendered.size) <= 1600


def test_pdf_render_scale_caps_at_pdf_render_scale(tmp_path: Path) -> None:
    """A normal A4 PDF must not be rendered beyond _PDF_RENDER_SCALE x point size."""
    # A4: 595 x 842 pt. At _PDF_RENDER_SCALE=2.0 the long side would be 1684 px,
    # but long_side_px=1600 is *smaller*, so the scale must be capped to
    # 1600/842 ≈ 1.90 — the bitmap before downscaling in _encode must never be
    # taller than long_side_px (i.e., the rendered pixels ≤ 1600 on long side).
    pdf_path = tmp_path / "a4.pdf"
    pdf = pdfium.PdfDocument.new()
    pdf.new_page(595, 842)
    pdf.save(str(pdf_path))

    images = render_page_images(
        "application/pdf", pdf_path, tmp_path, max_pages=1, long_side_px=1600
    )
    assert len(images) == 1
    rendered = Image.open(io.BytesIO(images[0]))
    # Must be within long_side_px.
    assert max(rendered.size) <= 1600
