# Receipt / thin-scan extraction hardening

**Date:** 2026-07-14. **Trigger:** production document 150 — a scanned
restaurant receipt that extracted confidently wrong. Engineering-team cycle
(discussion → plan → W1-W5), run `manual-20260714T194740Z`.

## 1. The failure

Doc 150 (1-page image PDF, thermal receipt): tesseract produced 460 garbled
chars (confidence 72) containing no date and no merchant name. Extraction
(Haiku, text mode) got the amount and kind right, left `document_date` null,
upserted the generic sender "Restaurant", and self-reported **high**
confidence — so the 2026-07-09 vision escalation never fired. Five seconds
later the markdown stage's vision call recovered "RESTAURANT GESTRAND" and
"13.07.2026" into `pages_markdown`, where nothing used them. Validation
produced zero findings (no missing-date rule; `missing_sender` doesn't fire
on a *set-but-generic* sender), so the document sat `unreviewed`.

Contrast doc 144 (garage receipt, 321 chars): self-reported low confidence →
escalated to vision → perfect extraction for $0.025. The machinery worked;
the trigger was the gap.

## 2. Decision: no receipt route

Receipts are ~22% of the corpus, but the failure class is "thin scanned
document", not "receipt". A dedicated route would need a pre-extraction
classifier (kind is an extraction *output*), invite per-kind pipeline forks,
and serve no consumer — nothing in the product uses line items; the receipt
contract is merchant + date + total + currency. We hardened the generic
route with kind-conditional behavior instead, extending the existing
`_MONETARY_KINDS` pattern. Explicitly rejected: pipeline reorder (markdown
before extract), structured line-item/VAT extraction, tesseract PSM tuning.

## 3. What shipped

1. **`missing_date` validation rule (W1)** — monetary kind + amount set +
   no `document_date` → `needs_review`. The date analogue of
   `missing_amount`; makes doc-150-shaped failures visible at zero cost.
2. **`generic_sender` rule + prompt hardening (W2)** — new
   `_GENERIC_SENDER_NAMES` blocklist (bilingual NL/EN category words),
   full-string casefold match only ("Garage Spaarndam" never fires).
   `validate()` gained a **required** `sender_name` kwarg (required, not
   defaulted, so a missed call site fails loudly); callers resolve it via
   `session.get(Sender, ...)`, never the lazy relationship. Extraction
   prompt now demands the printed merchant name, never a category word;
   `PROMPT_VERSION` → 2026-07-14.1 (makes the corpus stale for backfill).
3. **Density-based vision trigger (W3)** — new setting
   `extraction_vision_min_chars_per_page` (default 800, 0 disables). When
   OCR actually ran (`ocr_confidence IS NOT NULL` — born-digital docs are
   exempt because theirs is NULL) and stripped chars/page falls below the
   threshold, the PRIMARY extraction call sends the original file (vision)
   via the existing `force_file` machinery. Falls back to text when the
   file is unsendable, so no document that extracts today can regress.
   Calibration: failing scans ran 321-460 chars/page; scanned letters
   ~2700 and contracts ~10000 stay on text. Deliberately NOT exposed in
   the admin settings surface — every sibling threshold is env-only.
4. **Fill-only repair pass (W4)** — new `library/extraction/repair.py`,
   run at the tail of the markdown stage (`run_markdown` + the
   `markdown_document` backfill task; no new DocumentStatus). When
   findings include `missing_date`/`missing_sender`/`generic_sender` and
   `pages_markdown` exists, one Haiku structured call over the markdown
   fills ONLY null scalars (never user-edited); a sender may additionally
   be *replaced* when its current name is on the generic blocklist and
   the repair result is not (old row kept). Idempotent per
   `REPAIR_PROMPT_VERSION`; spend counts toward the extraction daily
   budget (`EXTRACTION_SPEND_EVENTS` sums `extraction_completed` +
   `extraction_repair_completed`); events
   `extraction_repair_completed`/`_skipped` with ordered skip reasons
   (disabled → missing_api_key → no_markdown → no_extraction →
   already_repaired → no_gaps → budget → error). Best-effort: a repair
   exception can never fail the markdown stage.
5. **Docs (W5)** — ingestion.md (rules table, input selection, repair
   subsection, post-deploy sweep note), architecture.md pipeline
   description, CHANGELOG entry, .env.example.

## 4. Key implementation notes

1. The repair pass's safety is structural: markdown hallucinations (doc
   150's own markdown shifted line-item prices) can only land in fields
   that were empty anyway.
2. The density decision lives in `extract()` reusing `force_file` — no
   `build_user_content` signature change; escalation composes for free
   (a vision primary short-circuits the rebuild guard).
3. The wrap-up docs audit caught that `POST /api/documents/{id}/extract`
   re-runs extraction only (not markdown/repair) — the post-deploy note
   now says to use `library backfill` for the full path.
4. Test suite grew 1183 → 1209 (all green, coverage 89%). One
   pre-existing markdown budget-isolation test was made delta-based (it
   asserted absolute-zero spend in the shared session-scoped DB and
   failed under subset ordering).

## 5. Follow-ups

1. **Post-deploy sweep** (documented in ingestion.md): run
   `library backfill-validation`, then `library backfill --kinds receipt`;
   verify doc 150 repairs; manually merge/delete the prod "Restaurant"
   sender row after repair re-points its documents.
2. **Frontend timeline label** (flagged, out of scope): the document
   history view infers "OCR was unusable → original file sent" from
   `escalated=false, input_mode=document`; a density-triggered primary
   now also matches that shape and gets mislabeled. Cosmetic; needs a
   small frontend change if it grates.
