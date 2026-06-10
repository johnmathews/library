"""Photo OCR path: OpenCV preprocessing + RapidOCR (PP-OCRv5 latin, ONNX CPU).

Preprocessing: grayscale -> page contour detection (largest 4-point contour
covering at least ``MIN_PAGE_AREA_FRACTION`` of the frame) -> 4-point
perspective transform when a page is found -> CLAHE contrast enhancement.

Recognition: RapidOCR with the PP-OCRv5 ``latin`` recognition model (one
model covers Dutch and English) on ONNX Runtime. Models are downloaded and
cached by rapidocr on first engine construction, hence the lazy cached
``get_engine()``. Boxes are sorted by (top-y, left-x) for reading order;
the document confidence is the mean of per-box scores scaled to 0-100 to
match Tesseract's scale.

This path never produces a searchable PDF (``searchable_pdf=None``).
"""

import logging
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import cv2
import numpy as np
import pypdfium2 as pdfium

from library.ocr.base import OcrResult

if TYPE_CHECKING:
    from rapidocr import RapidOCR

logger = logging.getLogger(__name__)

ENGINE_NAME: str = "rapidocr"
MIN_PAGE_AREA_FRACTION: float = 0.3
RASTER_DPI: int = 300


@lru_cache(maxsize=1)
def get_engine() -> "RapidOCR":
    """The shared RapidOCR engine (lazy: first construction downloads models)."""
    from rapidocr import EngineType, LangRec, ModelType, OCRVersion, RapidOCR

    return RapidOCR(
        params={
            "Det.engine_type": EngineType.ONNXRUNTIME,
            "Det.ocr_version": OCRVersion.PPOCRV5,
            "Cls.engine_type": EngineType.ONNXRUNTIME,
            "Rec.engine_type": EngineType.ONNXRUNTIME,
            "Rec.lang_type": LangRec.LATIN,
            "Rec.ocr_version": OCRVersion.PPOCRV5,
            "Rec.model_type": ModelType.MOBILE,
        }
    )


def ocr_image(image_path: Path) -> OcrResult:
    """OCR one image file (JPEG/PNG or the HEIC-derived converted.jpg)."""
    data = np.fromfile(str(image_path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"could not decode image: {image_path}")
    text, confidence = _ocr_array(image)
    return OcrResult(
        text=text,
        confidence=confidence,
        searchable_pdf=None,
        engine=ENGINE_NAME,
        pages=1,
    )


def ocr_pdf_pages(pdf_path: Path) -> OcrResult:
    """OCR a PDF page by page through the photo path (confidence-gate retry)."""
    page_texts: list[str] = []
    page_confidences: list[float] = []
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        pages = len(document)
        for index in range(pages):
            pil_image = document[index].render(scale=RASTER_DPI / 72).to_pil()
            array = cv2.cvtColor(np.asarray(pil_image.convert("RGB")), cv2.COLOR_RGB2BGR)
            text, confidence = _ocr_array(array)
            if text:
                page_texts.append(text)
            if confidence is not None:
                page_confidences.append(confidence)
    finally:
        document.close()
    return OcrResult(
        text="\n\n".join(page_texts),
        confidence=sum(page_confidences) / len(page_confidences) if page_confidences else None,
        searchable_pdf=None,
        engine=ENGINE_NAME,
        pages=pages,
    )


def _ocr_array(image: np.ndarray) -> tuple[str, float | None]:
    """Preprocess and recognize one image; returns (text, confidence 0-100)."""
    processed = preprocess(image)
    result = get_engine()(processed)
    txts: Sequence[Any] | None = getattr(result, "txts", None)
    if not txts:
        return "", None
    ordered = sort_reading_order(result.boxes, txts, result.scores)
    text = "\n".join(item_text for item_text, _ in ordered)
    confidence = 100.0 * sum(score for _, score in ordered) / len(ordered)
    return text, confidence


def preprocess(image: np.ndarray) -> np.ndarray:
    """Grayscale -> perspective-correct the page if found -> CLAHE contrast."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    quad = find_page_contour(gray)
    if quad is not None:
        gray = four_point_transform(gray, quad)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(gray)


def find_page_contour(gray: np.ndarray) -> np.ndarray | None:
    """The largest 4-point contour covering >= 30% of the frame, or None."""
    height, width = gray.shape[:2]
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edged = cv2.Canny(blurred, 50, 150)
    edged = cv2.dilate(edged, np.ones((3, 3), dtype=np.uint8), iterations=1)
    contours, _ = cv2.findContours(edged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best: np.ndarray | None = None
    best_area = MIN_PAGE_AREA_FRACTION * height * width
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < best_area:
            continue
        perimeter = cv2.arcLength(contour, closed=True)
        approx = cv2.approxPolyDP(contour, epsilon=0.02 * perimeter, closed=True)
        if len(approx) == 4:
            best = approx.reshape(4, 2).astype(np.float32)
            best_area = area
    return best


def _order_corners(quad: np.ndarray) -> np.ndarray:
    """Order 4 corners as top-left, top-right, bottom-right, bottom-left."""
    ordered = np.zeros((4, 2), dtype=np.float32)
    sums = quad.sum(axis=1)
    diffs = np.diff(quad, axis=1).reshape(-1)
    ordered[0] = quad[np.argmin(sums)]  # top-left: smallest x+y
    ordered[2] = quad[np.argmax(sums)]  # bottom-right: largest x+y
    ordered[1] = quad[np.argmin(diffs)]  # top-right: smallest y-x
    ordered[3] = quad[np.argmax(diffs)]  # bottom-left: largest y-x
    return ordered


def four_point_transform(gray: np.ndarray, quad: np.ndarray) -> np.ndarray:
    """Warp the quadrilateral page region to a straight-on rectangle."""
    top_left, top_right, bottom_right, bottom_left = _order_corners(quad)
    width = int(
        max(
            np.linalg.norm(bottom_right - bottom_left),
            np.linalg.norm(top_right - top_left),
        )
    )
    height = int(
        max(
            np.linalg.norm(top_right - bottom_right),
            np.linalg.norm(top_left - bottom_left),
        )
    )
    destination = np.array(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
        dtype=np.float32,
    )
    source = np.array([top_left, top_right, bottom_right, bottom_left], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(source, destination)
    return cv2.warpPerspective(gray, matrix, (width, height))


def sort_reading_order(
    boxes: Sequence[Any], txts: Sequence[Any], scores: Sequence[Any]
) -> list[tuple[str, float]]:
    """(text, score) pairs sorted by box top-y, then left-x (reading order)."""
    items: list[tuple[float, float, str, float]] = []
    for box, text, score in zip(boxes, txts, scores, strict=True):
        corners = np.asarray(box, dtype=np.float32)
        items.append(
            (float(corners[:, 1].min()), float(corners[:, 0].min()), str(text), float(score))
        )
    items.sort(key=lambda item: (item[0], item[1]))
    return [(text, score) for _, _, text, score in items]
