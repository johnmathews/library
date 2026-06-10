"""Rasterization must clamp pixel output so oversized page boxes can't
trigger Pillow's decompression-bomb guard (found by the W5 benchmark on a
real sample whose page box rasterizes to >200 MP at 300 dpi)."""

from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

from library.ocr.raster import MAX_RASTER_PIXELS, render_page


def _pdf_with_page_size(path: Path, width_pt: float, height_pt: float) -> Path:
    document = pdfium.PdfDocument.new()
    document.new_page(width_pt, height_pt)
    document.save(str(path))
    document.close()
    return path


def test_normal_page_renders_at_requested_dpi(tmp_path: Path) -> None:
    # A4: 595 x 842 pt -> ~2480 x 3508 px at 300 dpi, far below the cap.
    pdf = _pdf_with_page_size(tmp_path / "a4.pdf", 595, 842)
    document = pdfium.PdfDocument(str(pdf))
    try:
        image = render_page(document[0], dpi=300)
    finally:
        document.close()
    assert isinstance(image, Image.Image)
    assert 2470 <= image.width <= 2490
    assert image.width * image.height < MAX_RASTER_PIXELS


def test_oversized_page_is_clamped_not_bombed(tmp_path: Path) -> None:
    # 10000 x 10000 pt at 300 dpi would be ~1.7 gigapixels.
    pdf = _pdf_with_page_size(tmp_path / "huge.pdf", 10_000, 10_000)
    document = pdfium.PdfDocument(str(pdf))
    try:
        image = render_page(document[0], dpi=300)
    finally:
        document.close()
    assert image.width * image.height <= MAX_RASTER_PIXELS
    # Pillow's own guard must never be the thing that saves us.
    assert image.width * image.height < Image.MAX_IMAGE_PIXELS
