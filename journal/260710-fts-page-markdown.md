# Full-text search reads the vision "understood layer"

**Date:** 2026-07-10
**Branch:** `worktree-eng-fts-page-markdown` → `main`
**Cycle:** engineering-team (evaluate → plan → develop → wrap-up)

## What & why

Closed the last gap from the `ocr_text`-as-canonical audit (see
`260710-amount-grounding-vision-false-positive.md`). For a thin-OCR image PDF, raw
`ocr_text` is only the letterhead; the real body (amounts, line items, addresses,
prose) lives in the vision-generated per-page `DocumentPage.markdown`. Semantic
search and Ask already read that markdown, but **plain full-text search did not** —
its two generated tsvector columns indexed `title || summary || ocr_text || topics`.
So an exact invoice number or body phrase that only vision read was unfindable by
FTS, and `ts_headline` snippets were letterhead-only.

Now FTS indexes the body as `coalesce(pages_markdown, ocr_text)` — prefer the
understood layer, fall back to raw OCR — mirroring the embed/Ask retrieval rule.

## Key decision — option (a), denormalize + coalesce

A Postgres generated column can only read same-row columns, so it can't reach the
child `document_pages` table. Three options were surfaced to the product owner:
(a) denormalize the concatenated page markdown onto a `documents` column and add it
to the FTS expression; (b) a DB trigger on `document_pages`; (c) a separate
page-level index unioned at query time. **Chosen: (a).** It keeps the query path
essentially unchanged (search.py barely moves), all sync logic stays in Python and
unit-testable, and it mirrors the pattern the codebase already trusts.

Crucially the expression uses **`coalesce(pages_markdown, ocr_text)` (either/or), not
a concatenation of both** — so born-digital docs and notes (where
`pages_markdown == ocr_text`) are indexed once, no double-count / rank inflation.

## What shipped

- **`documents.pages_markdown`** (new nullable text column) — a search-only mirror
  of the concatenated per-page markdown; `document_pages` stays the source of truth.
- **`FTS_EXPRESSION`** body term → `coalesce(pages_markdown, ocr_text, '')`; snippet
  `ts_headline` reads the same source (`search.py`).
- **Migration `0025_fts_page_markdown`** — adds the column, backfills it from
  `document_pages` (`string_agg(markdown, E'\n\n' ORDER BY page_number)`), then
  drops/recreates both STORED generated tsvector columns + GIN indexes under the new
  expression (self-backfilling, modelled on `0012_topics_fts`). Reversible downgrade.
- **App-side sync** — one shared helper `markdown.apply.document_markdown_text`
  wired into the **three** places page rows are written: `apply_markdown`'s vision
  path and born-digital passthrough, and `api/notes.py` `_apply_body`. (notes.py was
  the easily-missed third surface — it writes `DocumentPage` directly, bypassing
  `apply_markdown`.) The helper's `\n\n` delimiter matches the migration backfill.

## Tests

+9 backend tests (suite 1084 → 1093):
- FTS finds a phrase present only in `pages_markdown` (thin OCR); snippet drawn from
  markdown; OCR fallback preserved when `pages_markdown` is NULL; no double-count
  (a `markdown == ocr_text` doc ranks identically to an OCR-only twin).
- Note create → findable by body FTS immediately (end-to-end `_apply_body` sync).
- Per-surface `pages_markdown` assertions on the vision + born-digital apply tests.
- **Migration backfill-on-data test** — migrates to 0024, inserts pre-0025-shaped
  rows (pages + thin OCR), upgrades to head, asserts the mirror + regenerated FTS
  vector. The fresh-DB schema tests migrate an *empty* DB, so the `string_agg`
  backfill would otherwise never run against data (destination vs journey).

## Notable / gotchas

- **Test DBs migrate to head via Alembic** (not `create_all`), so migration 0025 is
  load-bearing for the entire suite, and the model + migration must agree exactly.
- **Worktree path slip:** initial edits used main-checkout absolute paths and landed
  on `main` instead of the worktree; caught immediately via `git status`, relocated
  the three files into the worktree, restored main to clean. No harm, but a reminder
  to use worktree-relative/worktree-absolute paths for all file ops in Phase 3.
- Changing `FTS_EXPRESSION` is a schema migration (drop/recreate STORED generated
  columns + GIN) — a real DDL rebuild that holds a lock; fine at library scale.

## Follow-up (post-deploy)

Deploy applies 0025 (adds column, backfills, rebuilds vectors). Verify on prod-shaped
data: take a known image-PDF invoice (e.g. doc 144) and search an exact body phrase
that was only in its markdown — confirm it surfaces with a body snippet.
