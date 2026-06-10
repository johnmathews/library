# W4 — OCR pipeline with routed engines

Implemented the routed OCR subsystem (`src/library/ocr/`) and wired it into
the `process_document` pipeline.

## What landed

- `library.ocr.base` — `OcrResult` (text, confidence 0–100, searchable_pdf,
  engine, pages) + `OcrEngine` Protocol.
- `library.ocr.router` — routes by MIME type: text-layer PDFs are extracted
  directly with pypdfium2 (threshold `LIBRARY_TEXT_LAYER_MIN_CHARS_PER_PAGE`,
  default 50 chars/page); image-only PDFs and TIFFs (wrapped to PDF via
  img2pdf, Pillow fallback) go through OCRmyPDF/Tesseract; JPEG/PNG and the
  HEIC-derived `converted.jpg` go through OpenCV + RapidOCR; `text/plain`
  is a passthrough read.
- `library.ocr.tesseract` — OCRmyPDF as a subprocess (`python -m ocrmypdf -l
  nld+eng --rotate-pages --deskew --clean --oversample 300 --skip-text
  --sidecar`), producing `searchable.pdf` (PDF/A) + `ocr.txt` in the derived
  dir.
- `library.ocr.photo` — grayscale → page-contour detection (4-point contour
  ≥ 30% of frame) → perspective transform → CLAHE → RapidOCR PP-OCRv5
  `latin` on ONNX Runtime; (y, x) reading-order sort.
- Pipeline: `jobs.run_ocr` runs the router via `asyncio.to_thread`, persists
  `ocr_text`/`ocr_confidence`/`page_count`/`searchable_pdf`, and records
  `ocr_completed` / `ocr_failed` ingestion events.

## Decisions

- **Confidence probe**: OCRmyPDF doesn't report word confidence, so up to 3
  pages of the *produced* searchable PDF (post rotate/deskew) are rasterized
  at 300 dpi and probed with `tesseract … tsv`; mean word `conf` is the
  document confidence. Subprocess + TSV over pytesseract: same call, one
  fewer dependency.
- **Confidence gate**: below `LIBRARY_OCR_CONFIDENCE_THRESHOLD` (65.0, from
  the inception research) the PDF pages are retried through the photo path;
  the higher-confidence result wins; the Tesseract `searchable.pdf` is kept
  as the viewing artifact either way.
- **RapidOCR package**: the current package is `rapidocr` (v3.x; the old
  `rapidocr_onnxruntime` is the legacy 1.x line) with `onnxruntime` installed
  separately. Model selection: `Rec.lang_type=LangRec.LATIN` +
  `Rec.ocr_version=OCRVersion.PPOCRV5` (the `latin_PP-OCRv5_mobile_rec`
  model covers Dutch+English in one model). Models download on first engine
  init and are cached, hence the lazy `get_engine()`.
- **opencv**: rapidocr hard-depends on `opencv-python` (GUI build, needs
  libGL); a uv `override-dependencies` entry swaps it for
  `opencv-python-headless`, keeping the slim image GUI-free.
- **OCRmyPDF as subprocess**, not Python API: its API drives its own process
  pool and is awkward from a worker thread; a subprocess is isolated and
  gives clean stderr for error events.
- **Test fixtures are generated, not checked in**: fpdf2 for born-digital
  PDFs, Pillow + img2pdf for image-only PDFs/photos (Pillow ≥ 10.1 embedded
  scalable default font, so no system fonts needed).
- Real-engine tests are marked `slow_ocr`: the Tesseract one needs
  tesseract+gs+unpaper (CI installs them; locally it falls back to `eng` if
  `nld` tessdata is absent and skips if binaries are missing); the RapidOCR
  one skips if model download/init fails, so flaky networks can't break CI.

## Deviations from plan

- `tessdata_best` not pinned: the Docker image uses Debian's standard
  `tesseract-ocr-nld/-eng` packages. Swapping in tessdata_best is a W5
  (benchmark) tuning decision.
