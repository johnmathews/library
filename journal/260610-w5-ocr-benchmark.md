# W5 — OCR benchmark on real samples, scan-aware routing, gate fix

**Date:** 2026-06-10
**Unit:** W5 (improvement plan §1.3.5)

## What happened

Ran `scripts/ocr_benchmark.py` inside the production Docker image
(aarch64, docker-on-mac) over 16 real documents — 10 of them iOS Notes
scan exports, the primary input type. Committed report:
`docs/benchmarks/260610-ocr-benchmark.md` (anonymized; the full JSON with
document text stays out of the repo).

## What the data said

1. **Every document has a text layer**, including all 10 scans — Apple
   embeds its own invisible OCR text on export, and excerpts showed it
   contains garbage fragments. The W4 router trusted any text layer
   ≥ 50 chars/page, so production would never have OCRed the user's
   scans at all.
2. **Forced OCR on the stripped scans is good**: Tesseract word
   confidence 83.0–95.3 (median 91.9), 8.8–93.8 s/doc; RapidOCR
   near-constant 97–99 box confidence at 1.6–5.4 s/doc with similar
   char counts.
3. **The confidence gate was comparing incomparable scales** (Tesseract
   word conf vs RapidOCR box conf ~98 always): any triggered retry would
   always have "won" regardless of quality. Latent only because no real
   sample dipped below the 65.0 threshold.

## What changed

- **`library.ocr.analysis`** (new): image-backed-page / scan-like
  detection, moved from the benchmark script into production and
  imported back by the script. Page is image-backed when one raster
  image covers ≥ 50% of the page; doc is scan-like when ≥ 50% of pages
  are image-backed. Validated 10/10 scans and 6/6 born-digital docs on
  the corpus.
- **Router**: scan-like PDFs are OCRed even with a text layer
  (`--redo-ocr` when embedded text exists, since `--skip-text` skips
  every page that has text); on OCRmyPDF failure with a usable text
  layer the router falls back to it (`engine="text-layer-fallback"`)
  instead of failing the document. Born-digital routing unchanged.
- **Tesseract engine**: `ocr_pdf(..., redo=)` chooses flag sets.
  OCRmyPDF 17.5.0 rejects `--redo-ocr` with `--deskew`/`--clean-final`/
  `--remove-background` (verified in its option validator — plain
  `--clean` is allowed), so redo mode drops `--deskew`; scan-app exports
  are already deskewed so nothing of value is lost. Verified end-to-end
  that redo replaces an invisible junk text layer with fresh OCR.
- **Gate rule**: retry accepted iff it produced ≥ 0.8× Tesseract's
  character count; confidences are never compared across engines. Both
  raw confidences recorded in `OcrResult.gate` and the `ocr_completed`
  event detail.
- **Thresholds kept**: `ocr_confidence_threshold=65.0`,
  `text_layer_min_chars_per_page=50` — the data supports both once
  routing is scan-aware (see report §5.4).
- **Bug fixed**: a real sample's oversized page box rasterized to
  > 200 MP at 300 dpi, tripping Pillow's decompression-bomb guard.
  All rasterization now goes through `library.ocr.raster.render_page`
  (clamped to 40 MP, aspect-preserving).

## Test/fixture notes

New fixture `make_scanlike_pdf` (tests/ocr_fixtures.py): fpdf2 page with
a page-covering image plus a real text layer — pypdfium2 sees exactly
the iOS Notes shape, so router tests exercise the real detection (no
mocking of analysis). New `tests/test_ocr_analysis.py`; router tests
cover scan-like→redo, fallback-on-failure, failure-without-text-layer
re-raises, and the char-ratio gate (kept/rejected/exact-boundary).
Two pre-W5 gate tests that asserted cross-engine confidence comparison
were deliberately replaced by the text-yield rule tests.

## Caveats

Timings are aarch64 docker-on-mac; the target LXC is x86. Redo-mode
timings on the real corpus were not separately measured (strip-and-re-OCR
numbers are the proxy).
