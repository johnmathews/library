# ocr_confidence_gate vision gate + parallel e2e in CI

**Date:** 2026-07-10

Two small follow-ups after the `amount_grounding` vision false-positive fix (see
[260710-amount-grounding-vision-false-positive.md](260710-amount-grounding-vision-false-positive.md)).

## 1. ocr_confidence_gate — suppress when extraction read the image

The audit that produced the `amount_grounding` fix found one *other* validation rule of
the same family: **`ocr_confidence_gate`** (`extraction/validation.py`) flags "OCR confidence
below floor" as needs-review, with no `input_mode` check. When the accepted extraction read
the page **image** (vision fallback / born-unusable-OCR, `input_mode` `document`/`image`), the
OCR text was *not* the input we used, so its confidence says nothing about the result —
flagging it is noise of exactly the kind the user hit on doc 144.

Fix: gate the rule on the same `read_the_image` condition already computed for
`amount_grounding` — suppress when `input_mode` ∈ {document, image}; keep firing for
`text`/legacy. It didn't mis-fire on doc 144 (whose OCR confidence was a high 90 — OCR was
"sure" about its 321 thin chars and silently skipped the image body), but it's a latent false
positive for any image PDF whose OCR came back low-confidence.

Tests (`tests/test_extraction_validation.py`):
- `test_low_ocr_confidence_does_not_fire_when_extraction_read_the_image` (document/image).
- `test_low_ocr_confidence_still_fires_when_extraction_read_the_text` (input_mode text).
- Existing `test_low_ocr_confidence_fires` (no input_mode = legacy) still passes.

The two same-family fixes (`amount_grounding`, `ocr_confidence_gate`) are the complete set of
validation rules that assert something about `ocr_text` as if it were canonical; every other
text rule is cue-based (fires only on a *present* cue) and just stays silent on thin OCR. The
remaining `ocr_text`-as-canonical consumer is full-text search, tracked separately (a fresh
engineering-team cycle) — the FTS tsvector indexes `ocr_text`, not the vision markdown.

## 2. CI — run e2e in parallel

`e2e` was gated `needs: [backend, frontend]`, but it builds its own stack image and shares no
artifacts with those jobs — the gate was only fail-fast, and it serialized the two slowest
jobs (backend coverage → then e2e). Removed the `needs` so e2e runs concurrently:
wall-clock drops from ~`max(backend, frontend) + e2e` to ~`max(backend, frontend, e2e)`.

Safety unchanged: `promote` still `needs: [build, backend, frontend, e2e, compose-smoke]`, so
a broken unit suite still blocks deploy. `build` stays gated — it genuinely consumes the
backend/frontend **coverage artifacts**. Trade-off: a *red* run now spends a few e2e minutes
instead of skipping them, bounded by `cancel-in-progress` on the next push. Verified the
workflow YAML parses and the `needs` graph is coherent (e2e ungated, promote gate intact).
