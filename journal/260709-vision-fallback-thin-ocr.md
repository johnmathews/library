# Vision fallback for thin-OCR image PDFs

**Date:** 2026-07-09

## Why

Right after shipping the recipient/date extraction upgrade, a prod document
surfaced the next layer of the problem. Doc 144 (a Garage Spaarndam invoice,
image PDF) came in with a null amount, null date, and a recipient taken from the
email envelope rather than the document. Reading the original PDF as an image
showed all three fields plainly — **€ 144,19**, **Mevr. R. Mathews**,
**08-07-2026**. The OCR had captured only ~321 chars of letterhead/footer; the
invoice body never reached the model. And because the extractor's low-confidence
escalation re-ran on the *same* thin text, the bigger model was equally blind.

The recipient work was correct but starved of input: the hybrid ladder prefers
the document-stated recipient, but that name was never in the text the model saw.

## What shipped

1. **Vision escalation** (`extractor.py`). `build_user_content` gained a
   `force_file` flag that sends the original file even when OCR text exists.
   `extract()` now, on a low-confidence primary pass that ran on text, rebuilds
   the escalation input as the **original file** (vision) instead of re-sending
   the text — so an image PDF gets *read*. Stays two calls max; falls back to the
   text escalation when the original can't be sent (unusable mime / missing /
   oversized), so nothing regresses. `PROMPT_VERSION` → `2026-07-09.2`.
2. **`missing_amount` validation rule** (`validation.py`). A payment/due cue in
   the OCR text with a null `amount_total` → `needs_review`. Cheap safety net for
   the confident-but-wrong case the low-confidence trigger won't fire on.
3. **Docs/CHANGELOG** — ingestion.md input-selection + validation table, CHANGELOG.

## Decisions (with the user)

- **Trigger**: any low-confidence extraction with a usable original (not just
  null-amount), because the miss hits amount + recipient + date together.
- **Retry shape**: replace the sonnet-text escalation with sonnet-vision (2 calls
  max), not a third attempt.
- **Backfill**: yes, scoped, dry-run first.

## Scale / honesty

Currently rare: 1 of ~18 thin-OCR (<600 char) transactional PDFs also had a null
amount. This is quality polish, not a fire — but the fix is principled and cheap,
and it recovers the exact class of image invoice that defeated the recipient work.

## Verification

Extraction + validation + apply + cli + email + consume suites green (313 in the
affected slice); full suite run at wrap-up. New tests: low-confidence escalation
sends a `document`/`image` block (not text); unusable original falls back to text;
a high-confidence first pass never triggers a vision retry; `missing_amount` fires
on a payment cue + null amount and stays quiet otherwise.

## Follow-up for operators

After deploy: `library backfill --kinds invoice,receipt,utility-bill --dry-run`,
then run it. The vision fallback fires automatically on the low-confidence
re-extractions; most docs cost one haiku call, only the thin-OCR ones pay for a
vision retry. This recovers doc 144 and any siblings.
