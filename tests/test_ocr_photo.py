"""Unit tests for the photo OCR path: preprocessing and reading-order sort.

The RapidOCR engine itself is exercised only in the slow marked test
(tests/test_ocr_real.py); here everything is synthetic numpy data.
"""

import cv2
import numpy as np

from library.ocr import photo


class TestSortReadingOrder:
    def test_boxes_sorted_top_to_bottom_then_left_to_right(self) -> None:
        # Three boxes given out of order: bottom line, top-right, top-left.
        boxes = [
            [[10, 200], [110, 200], [110, 230], [10, 230]],  # bottom
            [[300, 20], [400, 20], [400, 50], [300, 50]],  # top right
            [[10, 22], [110, 22], [110, 52], [10, 52]],  # top left
        ]
        txts = ["bottom", "top-right", "top-left"]
        scores = [0.9, 0.8, 0.7]

        ordered = photo.sort_reading_order(boxes, txts, scores)

        assert [text for text, _ in ordered] == ["top-right", "top-left", "bottom"]

    def test_same_row_sorts_by_x(self) -> None:
        boxes = [
            [[500, 100], [600, 100], [600, 130], [500, 130]],
            [[10, 100], [110, 100], [110, 130], [10, 130]],
        ]
        ordered = photo.sort_reading_order(boxes, ["right", "left"], [0.5, 0.5])
        assert [text for text, _ in ordered] == ["left", "right"]

    def test_scores_travel_with_their_text(self) -> None:
        boxes = [
            [[0, 50], [10, 50], [10, 60], [0, 60]],
            [[0, 0], [10, 0], [10, 10], [0, 10]],
        ]
        ordered = photo.sort_reading_order(boxes, ["second", "first"], [0.2, 0.9])
        assert ordered == [("first", 0.9), ("second", 0.2)]


class TestPreprocess:
    def test_page_contour_is_perspective_corrected(self) -> None:
        # Dark frame with a bright axis-aligned "page" covering ~56% of it.
        image = np.zeros((400, 400, 3), dtype=np.uint8)
        cv2.rectangle(image, (50, 50), (350, 350), (255, 255, 255), thickness=-1)

        processed = photo.preprocess(image)

        assert processed.ndim == 2  # grayscale
        # Output is the warped page, not the full frame.
        assert processed.shape[0] < 400 and processed.shape[1] < 400
        assert abs(processed.shape[0] - 300) <= 10
        assert abs(processed.shape[1] - 300) <= 10
        # The warped page is the bright region.
        assert processed.mean() > 200

    def test_no_page_contour_keeps_full_frame(self) -> None:
        rng = np.random.default_rng(seed=1)
        image = rng.integers(100, 130, size=(200, 300, 3), dtype=np.uint8)

        processed = photo.preprocess(image)

        assert processed.ndim == 2
        assert processed.shape == (200, 300)

    def test_grayscale_input_accepted(self) -> None:
        image = np.full((100, 100), 128, dtype=np.uint8)
        processed = photo.preprocess(image)
        assert processed.shape == (100, 100)


class TestFindPageContour:
    def test_small_quad_is_ignored(self) -> None:
        # Bright square covering only ~6% of the frame: below the 30% gate.
        image = np.zeros((400, 400), dtype=np.uint8)
        cv2.rectangle(image, (100, 100), (200, 200), 255, thickness=-1)
        assert photo.find_page_contour(image) is None

    def test_large_quad_is_found(self) -> None:
        image = np.zeros((400, 400), dtype=np.uint8)
        cv2.rectangle(image, (40, 40), (360, 360), 255, thickness=-1)
        quad = photo.find_page_contour(image)
        assert quad is not None
        assert quad.shape == (4, 2)
