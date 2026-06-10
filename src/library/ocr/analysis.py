"""Scan detection: image-backed pages and scan-like PDFs.

The W5 benchmark (docs/benchmarks/260610-ocr-benchmark.md) found that every
iOS Notes scan export — the user's primary input type — carries an embedded
Apple-OCR text layer of mediocre quality. A router that trusts any text
layer therefore never OCRs the documents that need it most. The fix is to
look at what the pages *are*, not just whether text comes out:

- a page is **image-backed** when a single raster image object covers at
  least ``IMAGE_PAGE_AREA_FRACTION`` of the page area (a scan, or a scan-app
  export with text painted invisibly over the image);
- a PDF is **scan-like** when at least ``SCAN_LIKE_PAGE_FRACTION`` of its
  pages are image-backed.

Both detections were validated on the benchmark corpus: all 10 iOS Notes
exports are scan-like; all 6 born-digital documents (including a 28-page
report with 4 embedded scan pages) are not.
"""

from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw

# One image covering >= 50% of the page area marks the page as image-backed.
IMAGE_PAGE_AREA_FRACTION: float = 0.5
# >= 50% image-backed pages marks the document as scan-like.
SCAN_LIKE_PAGE_FRACTION: float = 0.5
# Image XObjects sit at depth 1; depth 2 also catches images nested in forms.
IMAGE_OBJECT_MAX_DEPTH: int = 2


@dataclass(frozen=True, slots=True)
class PdfAnalysis:
    """One pass over a PDF: text layer plus page composition."""

    page_chars: tuple[int, ...]
    image_backed_pages: int
    text: str

    @property
    def pages(self) -> int:
        return len(self.page_chars)

    @property
    def chars_per_page(self) -> float:
        return sum(self.page_chars) / self.pages if self.pages else 0.0

    @property
    def scan_like(self) -> bool:
        return self.pages > 0 and (self.image_backed_pages / self.pages >= SCAN_LIKE_PAGE_FRACTION)


def page_is_image_backed(page: pdfium.PdfPage) -> bool:
    """True when one raster image covers >= ``IMAGE_PAGE_AREA_FRACTION`` of the page."""
    width, height = page.get_size()
    threshold = IMAGE_PAGE_AREA_FRACTION * width * height
    for obj in page.get_objects(max_depth=IMAGE_OBJECT_MAX_DEPTH):
        if obj.type != pdfium_raw.FPDF_PAGEOBJ_IMAGE:
            continue
        left, bottom, right, top = obj.get_bounds()
        if (right - left) * (top - bottom) >= threshold:
            return True
    return False


def analyze_pdf(pdf_path: Path) -> PdfAnalysis:
    """Extract the text layer and classify pages in a single document pass."""
    page_chars: list[int] = []
    parts: list[str] = []
    image_backed = 0
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        for index in range(len(document)):
            page = document[index]
            text_page = page.get_textpage()
            try:
                text = (text_page.get_text_bounded() or "").strip()
            finally:
                text_page.close()
            page_chars.append(len(text))
            parts.append(text)
            if page_is_image_backed(page):
                image_backed += 1
    finally:
        document.close()
    return PdfAnalysis(
        page_chars=tuple(page_chars),
        image_backed_pages=image_backed,
        text="\n\n".join(part for part in parts if part).strip(),
    )
