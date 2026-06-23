# Markdown layer + page-aware citations

**Date:** 2026-06-21
**Sub-project:** 2 (+ folded-in 3) of 5 in the extraction/Ask improvement
track. Sub-project 1 (extraction quality — validation, review queue, eval
harness) was merged in the previous session.

---

## What shipped

### Task 1 — Config settings

Six new `LIBRARY_MARKDOWN_*` settings in `src/library/config.py`, each
with the `LIBRARY_` env prefix: `MARKDOWN_ENABLED` (bool, true),
`MARKDOWN_MODEL` (str, `claude-haiku-4-5`), `MARKDOWN_DAILY_BUDGET_USD`
(float, 5.0), `MARKDOWN_MAX_PAGES` (int, 20), `MARKDOWN_PAGE_BATCH` (int,
10), `MARKDOWN_IMAGE_LONG_SIDE_PX` (int, 1600). Tests in `tests/test_config.py`.

### Task 2 — Models + migration 0007

- `DocumentStatus.MARKDOWN = "markdown"` added to the StrEnum (between
  `EXTRACT` and `EMBED`; no DDL needed, the column is VARCHAR not a native
  enum).
- New `DocumentPage` SQLAlchemy model: table `document_pages`, PK
  `(document_id, page_number)`, columns `markdown` text, `char_count` int,
  `created_at` timestamptz. `Document.pages` relationship is `lazy="raise"` +
  `passive_deletes=True`.
- `DocumentChunk.page_number: int | None` column added.
- Migration `0007` (down_revision `0006`): creates `document_pages` +
  `ForeignKeyConstraint` (ON DELETE CASCADE) + adds `document_chunks.page_number`.
  No backfill in the migration itself (fast, reversible).

### Task 3 — Page renderer (`src/library/markdown/renderer.py`)

`render_page_images(mime_type, original_path, derived, *, max_pages,
long_side_px) -> list[bytes]`. Rasterizes with pypdfium2 + Pillow; encodes
to JPEG at 85 quality after downscaling to `long_side_px`.

**Security hardening added during implementation:**

The plan described a simple `_PDF_RENDER_SCALE = 2.0` upper bound, but the
final renderer uses `scale = min(_PDF_RENDER_SCALE, long_side_px /
long_side_pt)` — the scale is chosen to target the output size directly,
capped at 2.0. This means a maliciously large PDF page (huge point
dimensions) never produces a bitmap much larger than intended. Degenerate
zero-point pages render at 0.5 as a safe minimum.

For Pillow image branches, a `_MAX_IMAGE_PIXELS = 40_000_000` budget is
checked before decoding (`img.size` is header-only); images over the budget
are logged and skipped. `DecompressionBombError` is also caught and treated
the same way. Both paths return `[]`, leading to a `markdown_skipped
{reason: "input_unusable"}` event — the document is not failed.

### Task 4 — Schema + generator

`src/library/markdown/schema.py`: `PageMarkdown(page_number, markdown)`,
`DocumentMarkdown(pages: list[PageMarkdown])` — the structured-output format.

`src/library/markdown/generator.py`: `generate_markdown(document, ocr_text,
page_images, *, client, settings) -> MarkdownResult`. One
`client.messages.parse()` call per batch of `markdown_page_batch` images.
OCR text is truncated to 12 000 characters as a grounding reference.
Page numbers are assigned positionally and absolutely (model output sorted,
then re-numbered `offset + 1, offset + 2, …` clamped to the batch image
count) so a mis-numbered response can never invent a page without an image.
Cost is computed via the shared `estimate_cost_usd` from `library.extraction.extractor`.

### Task 5 — Apply stage (`src/library/markdown/apply.py`)

`apply_markdown(session, document, settings)` — guards (enabled check, key
check, budget check using `todays_markdown_spend_usd`), render call (with
try/except around the renderer itself), generate call, idempotent
delete+insert of `document_pages` rows.

Budget guard scoped to `event == "markdown_completed"` events only —
independent of the extraction budget (which is scoped to
`extraction_completed` events). The extraction budget query was also updated
in Task 5 to be explicit about its event name, preventing it from
accidentally counting markdown spend.

### Task 6 — Pipeline wiring

`_NEXT_STATUS` updated: `EXTRACT → MARKDOWN → EMBED`. `run_markdown` stage
hook added. `markdown_document` Procrastinate task added for re-generation
outside the pipeline.

### Task 7 — Page-aware embedding

`run_embed` now queries `document_pages` for the document:
- Pages found → chunk each page's `markdown`, tag each `DocumentChunk` with
  `page.page_number`.
- No pages → chunk `ocr_text` as before, `page_number = NULL`.
The `embedded` event gains `page_aware: bool`.

### Task 8 — `backfill-markdown` CLI

`library backfill-markdown [--limit N] [--include-existing]` in
`src/library/cli.py`. Enqueues `markdown_document` for eligible documents.
Default: only documents lacking any `document_pages` rows.

### Task 9 — `GET /api/documents/{id}/markdown`

Returns `{page_count: int, pages: [{page_number: int, markdown: str}]}` in
page_number order. Empty `pages` list (not 404) when no markdown exists for
the document.

### Task 10 — Ask page citations (backend)

`SemanticHit` in `library.search` gained `page_number: int | None` (the
`page_number` of the best-matching chunk). `AskCitation` gained
`page_number: int | None`. `_citations_for` builds a `document_id →
page_number` map from the top text hit per document; `query_documents`
citations always have `page_number = None`. The `/api/ask` response schema
carries `page_number` per citation.

### Task 11 — Frontend (AskView, DocumentDetailView)

`AskView.vue`: citations render as `Title, p. N` when `page_number` is set,
linking to the document detail at that page. `DocumentDetailView.vue`:
PDF iframe deep-links to `#page=N` when the citation carries a page number;
a new lazy markdown tab shows the assembled `document_pages` content.

---

## Decisions

**Hybrid vision + OCR grounding.** The key motivation for sending both page
images and the `ocr_text` blob: vision recovers layout and borderless
tables; OCR text anchors exact numbers, names, and codes so the model
hallucinates less. Neither alone is sufficient for household paperwork.

**Fold sub-project 3 in fully.** Page-numbered citations were originally a
separate sub-project (SP3). The spec folded SP3 into SP2 because vision
generates markdown per page and page provenance is then nearly free — and
both touch the chunk model, which is better changed once.

**`document_pages` table (not a flat column with sentinels).** A flat
"page-delimited OCR blob" or a JSONB field in `extra` were rejected: fragile
parsing, awkward to query, and `extra` is already crowded. A normalized table
with a composite PK is clean, queryable, and idempotent to replace.

**New pipeline stage `markdown`.** Adding it between `extract` and `embed`
means the embed stage always sees the freshest representation. The stage
follows the same best-effort contract as extraction — it must never fail a
document.

**Separate daily budget (`LIBRARY_MARKDOWN_DAILY_BUDGET_USD`).** The
extraction and markdown budgets are independent: different models, different
token counts, different value/cost ratios. Summing them into one cap would
make it impossible to control either independently.

**Default model `claude-haiku-4-5`.** Household tables are simple; the goal
is faithful markdown of structured data, not complex reasoning. Haiku is the
cheapest option that handles vision + structured output. No escalation this
phase.

**Positional/absolute page numbering.** The model is not trusted to number
pages correctly. Returned `PageMarkdown` entries are sorted by their reported
`page_number` and then re-assigned `offset + 1, offset + 2, …` clamped to
the batch image count. This tolerates a mis-numbered or short response without
ever inventing a page that has no source image.

**Renderer hardening.** The original spec described a simple `_PDF_RENDER_SCALE`
cap. The implementation went further: PDF scale is computed as
`min(_PDF_RENDER_SCALE, long_side_px / long_side_pt)` to target the output
size directly. Pillow image branches add a pixel-count budget check and catch
`DecompressionBombError`. Both ensure that corrupt or adversarially crafted
uploads cannot cause resource exhaustion or crash the worker.

---

## Schema changes (migration 0007)

- **New table `document_pages`**: PK `(document_id, page_number)`, FK
  `document_id → documents.id ON DELETE CASCADE`, columns `markdown text NOT
  NULL`, `char_count int NOT NULL`, `created_at timestamptz server default now()`.
- **New column `document_chunks.page_number int NULL`**: populated by the
  page-aware embed path; `NULL` when the chunk came from `ocr_text` or a page
  past the render cap.
- No backfill in the migration — `document_pages` starts empty; existing
  documents stay with `page_number = NULL` chunks until `backfill-markdown` is
  run.
- Reversible: `downgrade` drops `document_chunks.page_number` and
  `document_pages`.

---

## Follow-ups (not in this phase)

- **Markdown feeding extraction input.** Extraction currently uses `ocr_text`.
  Switching the extraction prompt to the rendered markdown (richer structure,
  real table data) could improve field accuracy — deferred until the markdown
  quality is validated on the real corpus.
- **Cross-encoder re-ranking.** RRF is the only fusion this phase; a
  cross-encoder re-ranking pass over the top-K semantic hits (using the
  markdown as the passage text) could improve retrieval quality.
- **Per-page OCR confidence.** The current OCR confidence is document-level.
  Per-page confidence (from the Tesseract TSV output or the RapidOCR box
  scores) would let the markdown stage skip or flag low-quality pages.
- **E2E tests require a live Anthropic key.** The Playwright e2e test for page
  citations (`ask a question → citation shows p. N → click deep-links PDF`) is
  gated on `E2E_BASE_URL` and a real API key. The CI environment does not have
  a key, so this test was structured to skip or be run manually. The rest of the
  e2e suite is green.
- **Accepted limitation: final DB-commit failure can fail the document.** If the
  `apply_markdown` call to `_record_event` (the very last commit, after all
  `document_pages` rows are inserted) fails, the document status may be left in
  an inconsistent state. This is the same edge case that exists in
  `apply_extraction` and is accepted for now.
- **Markdown editing UI.** No correction UI for the rendered markdown this phase.
  A future surface could let users fix a badly-rendered table and have it
  re-embed.
- **Prompt-change backfill automation.** The `PROMPT_VERSION` field is stored per
  run, so a query can identify stale renderings. Re-rendering them requires
  running `backfill-markdown --include-existing` manually; there is no automatic
  trigger on version bump.
