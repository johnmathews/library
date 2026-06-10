# W6 — Claude metadata extraction

**Date:** 2026-06-10. **Unit:** W6 (improvement plan 1.3.6, decision 3).

## What landed

- `anthropic` SDK 0.109.1 (`uv add anthropic`).
- `library.extraction` package:
  - `schema.py` — `ExtractedMetadata` Pydantic model used as the
    `output_format` for `client.messages.parse()` (structured outputs GA,
    validated instance via `response.parsed_output`). Kind slug is a
    `Literal` over the 11 seeded `kinds` rows, so the model cannot invent
    kinds. Dates are `date | None` with a before-validator (placeholder
    strings → None, malformed → ValidationError → escalation); amounts are
    decimal strings parsed defensively (`"€ 12,50"` → `"12.50"`, garbage →
    None); tags are slugified/deduped/capped at 8 client-side because
    structured outputs support no array/string-length constraints.
  - `extractor.py` — async `extract()`: versioned system prompt
    (`PROMPT_VERSION = "2026-06-10.1"`, kinds taxonomy, nld+eng household
    paperwork, title/summary in the document's language); OCR text
    truncated to 8k chars; primary `claude-haiku-4-5`, one escalation to
    `claude-sonnet-4-6` on `confidence == "low"` or parse failure. When
    OCR text is < 20 stripped chars, the original is sent as a base64
    `document` (PDF) / `image` block (HEIC via the derived
    `converted.jpg`), capped at 5 MB. Per-call token usage and cost (from
    static $/MTok pricing constants) are recorded.
  - `apply.py` — `apply_extraction()`: skip events for disabled /
    missing key / over budget / unusable input; sender upsert
    (case-insensitive), kind by slug, tag get-or-create + merge;
    `extra["extraction"]` provenance (prompt version, model, tokens,
    cost, `fields_set`); honours `extra["user_edited_fields"]` (W7/W11
    will populate it). **Extraction never fails a document** — every
    error path records an event and returns, so the pipeline always
    reaches `indexed`.
- `jobs.py` — `run_extraction` hook now real; new manually-deferrable
  `library.jobs.extract_document(document_id)` task for re-extraction.
- Settings: `LIBRARY_ANTHROPIC_API_KEY` (SecretStr), `_EXTRACTION_ENABLED`,
  `_EXTRACTION_MODEL`, `_EXTRACTION_ESCALATION_MODEL`,
  `_EXTRACTION_DAILY_BUDGET_USD` (default 5.0).
- Cost guard: one SQL query sums today's `detail->>'cost_usd'` over
  `ingestion_events`; at/over budget → `extraction_skipped` with
  `reason: "budget"`.

## Decisions and gotchas

- **No live API tests.** Extractor tests mock the SDK client object
  directly (`messages.parse` as `AsyncMock`) — HTTP-level mocking buys
  nothing here. Live smoke-test is a separate lead-engineer step.
- **`session.rollback()` expires ORM objects.** The failure path
  rollback made the later `document.id` access lazy-load synchronously
  inside the async session (`MissingGreenlet`). Fixed with
  `await session.refresh(document)` after rollback.
- **Shared test DB means unique fixture slugs.** The session-scoped
  `library_api` database is shared across integration tests; tag
  fixtures collided on `uq_tags_slug` until the tags test used its own
  slugs. Budget assertions use a per-test budget (1000.0) so one test's
  recorded spend can't starve another.
- Escalated runs record both calls' usage; the stored `model` is the one
  whose answer was used.

## Verification

`uv run pytest` — 110 passed, 1 warning in 7.23s;
`ruff check` + `ruff format --check` clean; `uv lock --check` OK.
