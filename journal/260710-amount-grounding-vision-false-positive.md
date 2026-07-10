# amount_grounding false positive on vision-read documents

**Date:** 2026-07-10

## 1. Report

Doc `/documents/144` was flagged "needs review" with the justification *"Amount not found
in the text — amount_total does not appear in the document text"*, yet the amount (144.19)
is plainly visible in the document's **markdown tile**. Confusing and wrong.

## 2. Root cause

Two different "texts" were being conflated:

- The **`amount_grounding`** validation rule (`extraction/validation.py`) checks
  `document.ocr_text` — the *raw OCR*.
- The **markdown tile** renders the per-page **vision-generated markdown** (a later,
  richer "understood" layer produced by `markdown/apply.py`).

Doc 144 is a **thin-OCR image PDF**: raw OCR captured only 321 chars (the letterhead), so
`144.19` never appears in `ocr_text`. Extraction recovered the amount via the **vision
fallback** (`extra.extraction` = `escalated=true, input_mode=document` — the model read the
page image, not the OCR text). Verified against prod:

- `ocr_text` digits = `…600114` (no `14419`/`144`), length 321
- page markdown digits contain `14419` ✓

`amount_grounding` is the *only* rule that asserts a **set** value must be present in the
OCR text; every other text rule is cue-based (a missing cue just makes it stay silent), so
this was the sole source of the false positive. Pipeline order is
`OCR → EXTRACT → MARKDOWN → EMBED` (`jobs.py`), so the vision markdown does not yet exist
when extraction's validation runs — validating against it isn't an option there.

Prod blast radius: exactly **1 document** (144) carried this finding.

## 3. Fix

Gate `amount_grounding` on the extraction having actually read the OCR text. When
`extra.extraction.input_mode` is `document`/`image` (the vision fallback or the
born-unusable-OCR first-attempt path), the amount was grounded in the *image*, so its
absence from thin OCR is expected — suppress the rule. Absent/legacy `input_mode` predates
the image paths (always text), so it keeps the check unchanged.

`input_mode` is already available at validation time: `_apply_outcome` writes
`extra["extraction"]` before `_apply_validation` runs, and `validate()` already reads that
dict for other rules.

## 4. Tests

`tests/test_extraction_validation.py`:
- `test_amount_absent_but_read_from_image_does_not_fire` — input_mode `document`/`image`,
  amount absent from ocr_text → **no** finding (regression for doc 144).
- `test_amount_absent_from_text_still_fires_when_ocr_text_was_the_input` — input_mode
  `text`, amount absent → still fires (a genuinely ungrounded amount).
- Existing "absent from text fires" (no input_mode = legacy) still passes → legacy default
  preserved.

Full backend suite: 1084 passing. ruff check + `format --check` clean.

## 5. Backfill

Re-validated doc 144 in prod after the fix shipped so the stale finding cleared without a
paid re-extraction (validation is deterministic and free).
