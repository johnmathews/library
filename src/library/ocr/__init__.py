"""Routed OCR: text-layer extraction, OCRmyPDF/Tesseract, OpenCV+RapidOCR.

See docs/ingestion.md ("OCR" section) for the routing rules and artifacts.
"""

from library.ocr.base import OcrEngine, OcrResult
from library.ocr.router import UnsupportedOcrInputError, run_ocr

__all__ = ["OcrEngine", "OcrResult", "UnsupportedOcrInputError", "run_ocr"]
