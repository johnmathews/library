"""Tesseract engine: OCRmyPDF subprocess + a word-confidence probe.

OCRmyPDF runs as a subprocess (``python -m ocrmypdf``) rather than through
its Python API: the API spawns its own process pool and is not designed to be
driven from a worker thread, while a subprocess is fully isolated and gives
us its stderr for error reporting.

OCRmyPDF does not expose word confidence, so after it succeeds a probe
rasterizes up to ``CONFIDENCE_SAMPLE_PAGES`` pages of the *produced*
searchable PDF (i.e. after ``--rotate-pages``/``--deskew``, close to what
Tesseract effectively saw) at 300 dpi and runs ``tesseract ... tsv`` on
them; the mean of per-word ``conf`` values is the document confidence.
Plain subprocess + TSV parsing was chosen over pytesseract because
pytesseract is the same subprocess call with an extra dependency.
"""

import logging
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

import img2pdf
import pypdfium2 as pdfium
from PIL import Image

from library.ocr.base import OcrResult

logger = logging.getLogger(__name__)

ENGINE_NAME: str = "tesseract"
SEARCHABLE_PDF_NAME: str = "searchable.pdf"
SIDECAR_NAME: str = "ocr.txt"
CONVERTED_PDF_NAME: str = "converted.pdf"
CONFIDENCE_SAMPLE_PAGES: int = 3
RASTER_DPI: int = 300


class TesseractError(RuntimeError):
    """OCRmyPDF (or the tesseract probe) failed."""


def ocr_pdf(pdf_path: Path, derived: Path, *, languages: str) -> OcrResult:
    """OCR a PDF with OCRmyPDF; write searchable.pdf + ocr.txt into ``derived``."""
    searchable = derived / SEARCHABLE_PDF_NAME
    sidecar = derived / SIDECAR_NAME
    command = [
        sys.executable,
        "-m",
        "ocrmypdf",
        "-l",
        languages,
        "--rotate-pages",
        "--deskew",
        "--clean",
        "--oversample",
        "300",
        "--skip-text",
        "--sidecar",
        str(sidecar),
        str(pdf_path),
        str(searchable),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise TesseractError(
            f"ocrmypdf exited {completed.returncode}: {completed.stderr.strip()[-2000:]}"
        )
    text = sidecar.read_text(encoding="utf-8", errors="replace").strip()
    pages = pdf_page_count(searchable)
    confidence = mean_word_confidence(searchable, languages=languages)
    return OcrResult(
        text=text,
        confidence=confidence,
        searchable_pdf=searchable,
        engine=ENGINE_NAME,
        pages=pages,
    )


def tiff_to_pdf(tiff_path: Path, derived: Path) -> Path:
    """Wrap a (possibly multi-frame) TIFF into a PDF for the OCRmyPDF path.

    img2pdf embeds the image losslessly; Pillow re-encodes but handles the
    TIFF variants img2pdf rejects (alpha channels, exotic compressions).
    """
    target = derived / CONVERTED_PDF_NAME
    try:
        target.write_bytes(img2pdf.convert(tiff_path.read_bytes()))
    except Exception:
        logger.info("img2pdf could not convert %s; falling back to Pillow", tiff_path)
        with Image.open(tiff_path) as image:
            frames: list[Image.Image] = []
            for index in range(getattr(image, "n_frames", 1)):
                image.seek(index)
                frames.append(image.convert("RGB"))
            frames[0].save(
                target, format="PDF", save_all=True, append_images=frames[1:], resolution=300
            )
    return target


def pdf_page_count(pdf_path: Path) -> int:
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        return len(document)
    finally:
        document.close()


def mean_word_confidence(
    pdf_path: Path,
    *,
    languages: str,
    max_pages: int = CONFIDENCE_SAMPLE_PAGES,
    dpi: int = RASTER_DPI,
) -> float | None:
    """Mean Tesseract word confidence over up to ``max_pages`` rasterized pages."""
    confidences: list[float] = []
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        sample = min(len(document), max_pages)
        with tempfile.TemporaryDirectory(prefix="ocr-conf-") as workdir:
            for index in range(sample):
                image = document[index].render(scale=dpi / 72).to_pil()
                page_png = Path(workdir) / f"page-{index}.png"
                image.save(page_png)
                completed = subprocess.run(
                    ["tesseract", str(page_png), "stdout", "-l", languages, "tsv"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if completed.returncode != 0:
                    logger.warning(
                        "confidence probe failed on page %s of %s: %s",
                        index,
                        pdf_path,
                        completed.stderr.strip()[-500:],
                    )
                    continue
                confidences.extend(parse_tsv_confidences(completed.stdout))
    finally:
        document.close()
    if not confidences:
        return None
    return statistics.fmean(confidences)


def parse_tsv_confidences(tsv: str) -> list[float]:
    """Per-word confidences from tesseract TSV output (level-5 rows, conf >= 0)."""
    confidences: list[float] = []
    lines = tsv.splitlines()
    for line in lines[1:]:  # skip header
        fields = line.split("\t")
        if len(fields) < 12 or fields[0] != "5":
            continue
        try:
            confidence = float(fields[10])
        except ValueError:
            continue
        if confidence < 0 or not fields[11].strip():
            continue
        confidences.append(confidence)
    return confidences
