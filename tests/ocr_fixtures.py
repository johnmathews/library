"""Helpers that generate OCR test fixtures on the fly.

Fixture strategy: nothing is checked in as binary. Born-digital (text-layer)
PDFs are generated with fpdf2 (already in the dependency tree via OCRmyPDF);
image-only PDFs are a Pillow-rendered image wrapped with img2pdf (exactly how
a scanner-app export looks to the pipeline); photos are Pillow-rendered
JPEG/PNG/TIFF. Pillow >= 10.1's ``ImageFont.load_default(size=...)`` provides
an embedded scalable font, so rendering needs no system fonts.
"""

import io
from pathlib import Path

import img2pdf
from fpdf import FPDF
from PIL import Image, ImageDraw, ImageFont


def make_text_pdf(path: Path, *, lines: list[str], pages: int = 1) -> Path:
    """A born-digital PDF with a real extractable text layer."""
    pdf = FPDF()
    pdf.set_font("helvetica", size=14)
    for _ in range(pages):
        pdf.add_page()
        for line in lines:
            pdf.cell(text=line, new_x="LMARGIN", new_y="NEXT")
    path.write_bytes(bytes(pdf.output()))
    return path


def render_text_image(
    text: str, *, size: tuple[int, int] = (2480, 600), font_size: int = 72
) -> Image.Image:
    """White page with black rendered text, roughly 300 dpi A4 width."""
    image = Image.new("L", size, color=255)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=font_size)
    draw.text((120, size[1] // 3), text, fill=0, font=font)
    return image


def make_image_pdf(path: Path, *, text: str = "Factuur 2026 rekening 12345") -> Path:
    """An image-only PDF (no text layer): rendered image wrapped by img2pdf."""
    image = render_text_image(text)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", dpi=(300, 300))
    path.write_bytes(img2pdf.convert(buffer.getvalue()))
    return path


def make_image(path: Path, *, text: str = "Factuur 2026 rekening 12345") -> Path:
    """A rendered photo-path input (format from the file suffix)."""
    image = render_text_image(text)
    image.save(path, dpi=(300, 300))
    return path


def make_scanlike_pdf(
    path: Path,
    *,
    lines: list[str] | None = None,
    pages: int = 1,
    image_pages: int | None = None,
) -> Path:
    """A scan-app-style PDF: full-page raster image PLUS an embedded text layer.

    This is the shape of an iOS Notes scan export (Apple embeds its own OCR
    text over the page image). Built with fpdf2: each "scanned" page draws a
    page-covering image and then writes the text lines on top — pypdfium2
    sees both a page-dominating image object and an extractable text layer.
    ``image_pages`` limits how many pages get the full-page image (the rest
    are text-only), for mixed-document fixtures; default: all pages.
    """
    if lines is None:
        lines = ["Factuur 2026 over de maand januari, rekening NL12RABO0123456789."] * 3
    image = Image.new("L", (1240, 1754), color=250)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    image_page_count = pages if image_pages is None else image_pages

    pdf = FPDF(unit="pt", format=(595, 842))
    pdf.set_auto_page_break(False)
    for index in range(pages):
        pdf.add_page()
        if index < image_page_count:
            buffer.seek(0)
            pdf.image(buffer, x=0, y=0, w=595, h=842)
        pdf.set_font("helvetica", size=12)
        pdf.set_xy(36, 36)
        for line in lines:
            pdf.cell(text=line, new_x="LMARGIN", new_y="NEXT")
    path.write_bytes(bytes(pdf.output()))
    return path
