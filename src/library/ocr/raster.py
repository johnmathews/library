"""Shared, bomb-safe PDF page rasterization.

Real documents occasionally carry absurd page boxes (the W5 benchmark hit a
sample that rasterizes to >200 MP at 300 dpi, tripping Pillow's
decompression-bomb guard). Every place that rasterizes a page for OCR goes
through ``render_page``, which clamps the render scale so the output stays
under ``MAX_RASTER_PIXELS`` while preserving aspect ratio.
"""

import math

import pypdfium2 as pdfium
from PIL import Image

# ~A2 at 300 dpi. Plenty for OCR accuracy; far below Pillow's ~179 MP guard.
MAX_RASTER_PIXELS: int = 40_000_000


def render_page(page: pdfium.PdfPage, *, dpi: int = 300) -> Image.Image:
    """Render a page at ``dpi``, scaling down if the result would exceed
    ``MAX_RASTER_PIXELS``."""
    width_pt, height_pt = page.get_size()
    scale = dpi / 72
    pixels = (width_pt * scale) * (height_pt * scale)
    if pixels > MAX_RASTER_PIXELS:
        # 0.995 absorbs the per-dimension round-up when pdfium sizes the bitmap.
        scale *= math.sqrt(MAX_RASTER_PIXELS / pixels) * 0.995
    return page.render(scale=scale).to_pil()
