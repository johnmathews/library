# W7 — Documents REST API, search, thumbnails

**Date:** 2026-06-10

## What shipped

The full documents REST surface under `/api` (docs/api.md is the
narrative reference, written before the code):

- `GET /api/documents` — pagination (default 25, max 100, `total` in the
  body), composable filters (kind slug, sender_id, repeatable `tag` =
  AND, language, status, document_date range, source), and `q` full-text
  search.
- `GET /api/documents/{id}` — full detail: OCR text/confidence, amounts
  and dates, extraction provenance (`extra["extraction"]` only, not the
  raw JSONB), the ingestion-event audit trail, sha256/source/filename.
- `PATCH /api/documents/{id}` — metadata edits (title, summary, dates,
  kind by slug, sender by name with the same case-insensitive upsert
  extraction uses, full-replacement tag list, language, amount/currency).
  Edited fields land in `extra["user_edited_fields"]` under their
  storage names (`kind_id`, `sender_id`), which W6 re-extraction already
  honours; a `user_edited` event records the change.
- `DELETE /api/documents/{id}` — soft delete (sets `deleted_at`,
  `deleted` event, 204); deleted documents 404 everywhere. Restore is
  out of scope (clear `deleted_at` manually).
- Downloads: original (real content type + attachment disposition with
  the original filename) and `searchable.pdf` (404 when absent).
- Thumbnails: `library.thumbnails` renders page 1 (pypdfium2 for PDFs,
  Pillow for images, HEIC via the derived `converted.jpg`) to a ~480px
  WebP `thumb.webp` in the derived dir. A `generate_thumbnail` job is
  deferred from the pipeline right after the OCR stage succeeds, so it
  runs in parallel with extraction. Presence is file existence — no
  schema change.

## Design decisions

- **Rank combination.** `q` is matched with `websearch_to_tsquery`
  against both generated vectors (dutch + english), OR-combined;
  ordering uses `greatest(ts_rank_nl, ts_rank_en)` so a document strong
  in either language wins. Verified with stemming tests both ways
  ("rekening" finds "rekeningen", "policy" finds "policies").
- **Snippets.** `ts_headline` over `ocr_text`, generated with whichever
  config ranked higher, capped at `MaxFragments=2, MaxWords=12`,
  default `<b>` markers. The OCR text is not HTML-escaped server-side;
  docs/api.md tells frontends to render snippets as text and interpret
  only the `<b>` markers.
- **Storage read paths.** Added `storage.derived_path()` (no mkdir) so
  GET endpoints and `has_thumbnail` checks don't create empty derived
  directories as a side effect.
- **Helper reuse.** Promoted `upsert_sender` / `get_or_create_tag` in
  `extraction/apply.py` to public names; PATCH uses the exact same
  upsert semantics as extraction.
- **OpenAPI as product surface.** App description, tag descriptions,
  response models on every endpoint, and `openapi_examples` on `q`;
  asserted in a test so the curation can't silently regress.

## Testing

29 new tests (test_documents_api.py, test_thumbnails.py, plus a
pipeline-wiring test): search ranking/stemming/snippet safety, filter
combinations, pagination totals, PATCH incl. an end-to-end W6-contract
check (PATCH then a mocked `apply_extraction` run does not overwrite the
edits), delete-404s-everywhere, download headers, real WebP generation
for a generated PDF fixture. Existing pipeline tests gained an open
in-memory Procrastinate connector (`job_connector` fixture in conftest)
because `advance_pipeline` now defers the thumbnail job.

Full suite: 140 passed. Auth remains the headline gap (W8 next).
