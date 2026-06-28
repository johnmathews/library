# W5 OCR benchmark — real document corpus

Benchmark of the W4 OCR pipeline over 16 of the user's real documents,
and the routing/gate changes it forced. All sample identifiers are
anonymized (`sample-NN` in sorted filename order); this report contains
no document content, filenames, or senders.

## 1. Environment

- python: 3.13.13
- platform: Linux-6.12.54-linuxkit-aarch64-with-glibc2.41 (the production
  Docker image, run via docker-on-mac)
- machine: aarch64
- tesseract: 5.5.0
- OCRmyPDF: 17.5.0; RapidOCR PP-OCRv5 `latin` mobile models on ONNX
  Runtime CPU
- settings: `ocr_languages=nld+eng`, `ocr_confidence_threshold=65.0`,
  `text_layer_min_chars_per_page=50`

## 2. Methodology

`scripts/ocr_benchmark.py` over a private samples directory of 16 real
PDFs (Dutch + English household paperwork; 10 of them iOS Notes scan
exports). For every document:

1. **Page analysis** — text-layer extraction (pypdfium2) plus
   image-backed-page classification: a page counts as image-backed when a
   single raster image covers ≥ 50% of the page area; a document is
   scan-like when ≥ 50% of its pages are image-backed. (This detection
   now lives in production as `library.ocr.analysis`.)
2. **Routing** — record the route the router would choose.
3. **OCR paths** — on OCR-routed documents, run the production Tesseract
   path (OCRmyPDF with the production flag set, then the TSV word-confidence
   probe) and the photo path (300 dpi rasterize → OpenCV preprocess →
   RapidOCR), both wall-clock timed.
4. **`--force-scans` strip-and-re-OCR** — scan-like documents that the
   (old) router would send to the text layer were additionally measured by
   *stripping* the embedded text layer first (every page rasterized at
   300 dpi and rebuilt into an image-only PDF) so `--skip-text` could not
   skip the pages. This measures what the OCR pipeline would have produced
   had the scan arrived without its embedded text, and is what the per-doc
   OCR numbers below are for the 10 scan-like samples.
5. **Gate simulation** — compute which engine the confidence gate would
   keep.

## 3. Results

### 3.1 Per-sample results (anonymized)

Measured with the pre-W5 router (every document below cleared the
text-layer threshold, so `route=text-layer` throughout; "OCR forced"
marks the strip-and-re-OCR runs on the scan-like samples).

| sample | pages | image-backed pages | text-layer chars/page | route | OCR forced | tess conf | tess chars | tess s | rapidocr conf | rapidocr chars | rapidocr s | gate pick |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| sample-01 | 9 | 0 | 1597 | text-layer | no | — | — | — | — | — | — | — |
| sample-02 | 1 | 1 | 1754 | text-layer | yes | 91.3 | 1753 | 13.0 | 98.7 | 1559 | 4.6 | tesseract |
| sample-03 | 24 | 0 | 1680 | text-layer | no | — | — | — | — | — | — | — |
| sample-04 | 5 | 0 | 1299 | text-layer | no | — | — | — | — | — | — | — |
| sample-05 | 28 | 4 | 2609 | text-layer | no | — | — | — | — | — | — | — |
| sample-06 | 1 | 1 | 2470 | text-layer | yes | 92.5 | 2346 | 93.8 | 98.5 | 2529 | 4.9 | tesseract |
| sample-07 | 1 | 0 | 1513 | text-layer | no | — | — | — | — | — | — | — |
| sample-08 | 1 | 0 | 1000 | text-layer | no | — | — | — | — | — | — | — |
| sample-09 | 1 | 1 | 1021 | text-layer | yes | 83.0 | 722 | 83.1 | 97.5 | 799 | 2.9 | tesseract |
| sample-10 | 1 | 1 | 1126 | text-layer | yes | 85.8 | 1271 | 85.3 | 98.2 | 1152 | 2.9 | tesseract |
| sample-11 | 1 | 1 | 1022 | text-layer | yes | 86.8 | 1077 | 14.7 | 98.7 | 1099 | 2.6 | tesseract |
| sample-12 | 2 | 2 | 2155 | text-layer | yes | 95.3 | 4288 | 19.9 | 98.7 | 4151 | 5.4 | tesseract |
| sample-13 | 1 | 1 | 619 | text-layer | yes | 92.2 | 601 | 13.1 | 98.7 | 724 | 1.6 | tesseract |
| sample-14 | 1 | 1 | 574 | text-layer | yes | 91.8 | 871 | 8.8 | 98.8 | 872 | 2.0 | tesseract |
| sample-15 | 1 | 1 | 2058 | text-layer | yes | 91.9 | 2032 | 13.3 | 97.1 | 1817 | 2.7 | tesseract |
| sample-16 | 1 | 1 | 2058 | text-layer | yes | 91.9 | 2032 | 13.3 | 97.1 | 1817 | 2.6 | tesseract |

### 3.2 Aggregates

- Documents: 16 (16 routed text-layer by the pre-W5 router, 0 routed OCR;
  OCR paths measured on 10 docs via forced strip-and-re-OCR)
- Scan-like docs (mostly image-backed pages with embedded text layer): 10
- Text-layer extraction seconds: n=16 min=0.0 median=0.0 mean=0.0 max=0.0
- Tesseract path seconds: n=10 min=8.8 median=14.0 mean=35.8 max=93.8
- Photo path seconds: n=10 min=1.6 median=2.8 mean=3.2 max=5.4
- Tesseract mean word confidence: n=10 min=83.0 median=91.9 mean=90.2 max=95.3
- RapidOCR mean box confidence: n=10 min=97.1 median=98.6 mean=98.2 max=98.8
- Gate decisions: 10× tesseract, 0× rapidocr (threshold 65.0)
- Photo retries the gate would trigger: 0/10
- chars/page among text-layer-routed docs: n=16 min=574.0 median=1555.1
  mean=1534.7 max=2609.2 (threshold: 50)
- Tesseract confidences vs gate threshold 65.0:
  [83.0, 85.8, 86.8, 91.3, 91.8, 91.9, 91.9, 92.2, 92.5, 95.3]
- RapidOCR confidences:
  [97.1, 97.1, 97.5, 98.2, 98.5, 98.7, 98.7, 98.7, 98.7, 98.8]

## 4. Findings

### 4.1 The router never OCRed the primary input type

All 16 documents have text layers — including all 10 iOS Notes scan
exports, where Apple embeds its own (invisible) OCR text on export.
Excerpt inspection of that embedded text showed garbage fragments. The
pre-W5 router trusted any text layer ≥ 50 chars/page, so in production
the user's primary input type would **never** be OCRed: the system would
silently inherit Apple's mediocre OCR for every scan.

### 4.2 Forced OCR on the 10 scans is good and affordable

With the embedded layer stripped, Tesseract scored mean word confidence
83.0–95.3 (median 91.9) at 8.8–93.8 s/doc (median 14 s) in this
environment; RapidOCR scored a near-constant 97.1–98.8 box confidence at
1.6–5.4 s/doc with similar character counts. Re-OCRing scans is clearly
worth it and well within job-queue latency tolerances.

### 4.3 The confidence gate compared incomparable scales

The gate compared Tesseract *word* confidence against RapidOCR *box*
confidence. RapidOCR's box confidence is essentially constant (~97–99
on every sample, regardless of actual quality), so any triggered retry
would always have "won" no matter what it actually read. The gate never
fired on this corpus (all Tesseract confidences ≥ 83 vs threshold 65),
which is the only reason this bug was latent.

## 5. Decisions

### 5.1 Scan-aware routing (implemented)

Page/document classification moved into production as
`library.ocr.analysis` (same logic the benchmark used; the benchmark now
imports it). New routing for `application/pdf`:

- text layer ≥ 50 chars/page AND not scan-like → text-layer extraction
  (unchanged; covers the 6 born-digital samples, including a 28-page
  report with 4 embedded scan pages — 4/28 < 50% keeps it text-layer);
- scan-like (with or without embedded text) → Tesseract path
  (covers all 10 scan samples);
- on OCRmyPDF/Tesseract **failure** with a usable embedded text layer,
  fall back to it with `engine="text-layer-fallback"` instead of failing
  the document.

### 5.2 `--redo-ocr` for scans with embedded text (implemented)

`--skip-text` skips every page that already has text, so re-OCRing a
Notes export needs `--redo-ocr`. OCRmyPDF 17.5.0 rejects `--redo-ocr`
combined with `--deskew`, `--clean-final` or `--remove-background`
(verified against its option validation; plain `--clean` remains allowed
and still cleans the image fed to Tesseract). The redo flag set therefore
drops `--deskew` — acceptable because scan-app exports are already
deskewed and cropped by the app. Verified end-to-end that `--redo-ocr`
replaces an invisible junk text layer with fresh Tesseract text.

### 5.3 Gate rule: text yield, not cross-engine confidence (implemented)

When Tesseract confidence < threshold and the photo path retries, the
retry is kept **iff it produced at least 0.8× Tesseract's character
count**; otherwise Tesseract's result is kept. Raw confidences are never
compared across engines. Both confidences are recorded
(`OcrResult.gate`, surfaced in the `ocr_completed` event detail); the
retained `confidence` stays on the chosen engine's own scale, with
`engine` naming which.

### 5.4 Thresholds: kept

- `ocr_confidence_threshold = 65.0` — kept. All 10 real scans scored
  83.0+, comfortably above; 65 still catches genuinely bad runs without
  triggering pointless retries (0/10 retries on this corpus).
- `text_layer_min_chars_per_page = 50` — kept. With scan-aware routing in
  place the threshold only decides born-digital vs sparse-text PDFs, and
  the data supports it: every text-layer-routed doc measured ≥ 574
  chars/page, an order of magnitude above the threshold.

## 6. Bug found and fixed: decompression bomb in rasterization

One sample carries an absurd page box that rasterizes to > 200 MP at
300 dpi, tripping Pillow's decompression-bomb guard and failing the
document. Fix: all OCR page rasterization now goes through
`library.ocr.raster.render_page`, which clamps the render scale so output
stays ≤ 40 MP (~A2 at 300 dpi, plenty for OCR) while preserving aspect
ratio. Regression-tested in `tests/test_ocr_raster.py`.

## 7. Caveats

- Timings were measured in the production Docker image but under
  docker-on-mac on aarch64; the deployment target is an x86 LXC.
  Absolute seconds will differ — the relative ordering (RapidOCR ~5–10×
  faster than the OCRmyPDF path) is the robust signal.
- The per-doc OCR numbers for scan-like samples come from the
  strip-and-re-OCR runs (`--skip-text` on an image-only copy), not from
  `--redo-ocr` on the original; redo mode was verified functionally but
  its timings on the real corpus are expected to be similar, not
  identical.
- `--redo-ocr` only replaces *invisible* text (OCR layers); visible text
  is preserved and masked out of OCR, so a scan-like PDF with visible
  text overlays would yield a sparse sidecar. iOS Notes exports embed
  invisible text, so this does not affect the target input type.
- RapidOCR box confidence is uninformative as a quality signal
  (~98 ± 1 on everything); never use it for cross-engine comparison.
