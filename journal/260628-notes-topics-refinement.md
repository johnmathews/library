# 1. Notes authoring + topics refinement

**Date:** 2026-06-28
**Branch:** `feat/notes-topics-refinement` (plain feature branch, merged to `main`)

## 1.1 Why

Follow-on to the general-document-store cycle (`journal/260628-general-document-store.md`).
That cycle left three threads dangling, all listed in `docs/roadmap.md §1.1`:
`topics` had become a confused parallel-to-`tags` editable field, there was no
way to author a note inside Library (and an edited note re-uploaded would
dedup-collide), and the existing corpus still carried the pre-upgrade extraction
prompt. This cycle resolves all of them, plus adds the browser-level e2e coverage
the new journeys never had. Design decisions were settled up front; this was an
implement-only run (TDD per unit, dispatched to subagents with disjoint file
boundaries, migrations serialized).

## 1.2 What shipped (6 units)

### 1.2.1 Topics → read-only + searchable (U1)

The topics-vs-tags question is now decided: **`tags`** are the curated,
low-cardinality, editable cross-document filter facet; **`topics`** are a
document's own auto-extracted subject list — read-only and searchable. Last
cycle's review fix that made `topics` editable was reversed: removed from
`DocumentUpdate`, the `PATCH /api/documents/{id}` handler, and the detail-view
editor, while kept in REST/MCP responses and the read-only badge display.

`topics` now feeds full-text search: `FTS_EXPRESSION` (the shared definition of
the two STORED generated tsvector columns `search_vector_nl`/`search_vector_en`)
gained `|| ' ' || coalesce(topics::text, '')`. The `::text` cast on JSONB is
IMMUTABLE, so it is valid inside a STORED generated column (a set-returning
`unnest` would not be). Migration `0012_topics_fts` drops and recreates both
columns + their GIN indexes, which recomputes every row (self-backfilling).

### 1.2.2 Notes — in-app authoring (U2)

New `DocumentSource.NOTE` and a dedicated router (`src/library/api/notes.py`):

- `POST /api/notes {title, body_markdown}` → a born-digital `text/markdown`
  document through the existing zero-API-cost text path (one `DocumentPage`).
- `PATCH /api/notes/{id} {title?, body_markdown?}` → in-place edit; snapshots the
  prior `(title, body)` into the new `note_versions` table, then re-runs
  extraction + markdown (+ embed).
- `GET /api/notes/{id}/versions` and
  `POST /api/notes/{id}/versions/{version_no}/restore`.

Key decisions:

1. **Dedup bypass via salted sha.** A note's `Document.sha256` is
   `sha256(body + uuid4())`, fixed for the note's life. The body file is written
   directly to `path_for(sha256)` (not via content-addressed `storage.store`).
   This structurally exempts notes from the SHA-256 content dedup, so identical
   or edited-back-to-identical bodies coexist, and an edited note never collides.
2. **Title locked.** The author's title is set and added to
   `extra.user_edited_fields`, so auto-extraction still fills
   summary/topics/tags/kind but never overwrites the title.
3. **Snapshot table, not a supersedes chain** for `note_versions`
   (`id, document_id, version_no, title, body, created_at`) — append-only,
   mirroring `ingestion_events`.

Migration `0013_notes` adds `'note'` to the `documents.source` CHECK and creates
`note_versions`. Notable: the codebase had **no** pre-existing CHECK on
`documents.source` (the `native_enum=False` enums never emitted one through the
migrations), so the migration uses idempotent raw DDL
(`DROP CONSTRAINT IF EXISTS` + `ADD CONSTRAINT`) and now actively enforces the
enum going forward.

### 1.2.3 Notes frontend (U3)

A "New note" view at `/notes/new` (title + markdown body + live
DOMPurify-sanitized preview reusing the existing `marked` renderer), a sidebar
link, in-place note editing in `DocumentDetailView` (title + body, gated on
`source === 'note'`), and a version-history disclosure with per-version restore.
New `api/notes.ts` client; `'note'` added to the `DocumentSource` union.

### 1.2.4 Backfill CLI (U5)

`library backfill` re-enqueues `extract → markdown → embed` for documents whose
`extra.extraction.prompt_version` is stale or missing, so the existing corpus
picks up the new prompt, long-doc sampling, `topics`, and markdown chunking.
Flags: `--limit`, `--general-only/--all-kinds` (default general-only =
`{manual, reference, research, note}`, so invoices aren't re-paid),
`--include-current`, `--dry-run`. The daily extraction budget is enforced
worker-side (`apply_extraction` skips over-budget docs, re-queueable next day);
the CLI only enqueues.

### 1.2.5 E2E coverage (U4)

Four new Playwright specs in `frontend/e2e/` for journeys that had zero
browser-level coverage: `.md` upload → reader card; create project → assign →
filter dashboard; create note → edit → restore version; topics render read-only.
They self-skip without `E2E_BASE_URL` and run against the real stack in CI's
`e2e` job.

### 1.2.6 Docs (U6)

Updated `docs/{api,ingestion,frontend,architecture,roadmap}.md` + `docs/mcp.md`
+ top-level `README.md`. Shipped items moved out of roadmap §1.1; the
topics-vs-tags open question (§1.3) resolved.

## 1.3 Discovered during wrap-up (code review + doc audit)

A dedicated code-review subagent over the full diff found three real issues, all
fixed before merge:

1. **Empty PATCH created a phantom version.** `update_note` snapshotted
   unconditionally; a no-op `PATCH {}` polluted the version history. Added the
   `if not provided: return` guard (mirroring `update_document`) + a regression
   test.
2. **Non-atomic note file write.** `_write_body` used `path.write_bytes`, which
   truncates-then-writes; a crash mid-edit could corrupt a note's only file.
   Switched to the atomic temp-file + `os.replace` pattern `storage.store` uses.
3. **Migration 0013 downgrade would fail with notes present.** Re-adding the
   narrower CHECK validates existing rows, so any `source='note'` row aborted the
   downgrade. The downgrade now hard-deletes note documents first (documented as
   destructive-by-design — there is no schema that can hold notes once `'note'`
   is invalid).

A follow-up gap review (does the webapp reach the new endpoints? docs fresh?
coverage?) confirmed the frontend↔backend wiring is correct (notes router mounted
at `/api` with auth+CSRF; `api/notes.ts` paths and `apiFetch` CSRF handling match)
and surfaced one more real issue, now fixed:

4. **Note reader was stale until the async worker caught up.** Create/edit/restore
   only deferred the markdown re-render to the worker, so the reader (which reads
   the `DocumentPage` rows) showed stale/empty content until the job ran — and
   indefinitely if the worker was down. Since a note's body is born-digital, the
   displayable layer (`ocr_text` + the single `DocumentPage`) is now **materialized
   synchronously** in the request; the worker handles only metadata extraction and
   embeddings (`extract_document` + `embed_document`). This also removes an e2e
   race that was masked by Playwright's retry. (The `notes.py` line-coverage figure
   reads low in isolation only because Starlette's TestClient runs async handlers in
   worker threads the default coverage tracer doesn't follow — every endpoint is
   exercised and asserted by `test_notes_api.py`.)

A doc-freshness audit subagent found and fixed four gaps: `topics` missing from
`docs/mcp.md` return shapes; note authoring missing from the `README.md` channel
list; an inaccurate "fifth ingestion channel" phrasing in `docs/ingestion.md`
(there are seven sources); and a `{n}` vs `{version_no}` placeholder mismatch in
the api.md endpoint summary.

## 1.4 Tests & verification

- Backend: **600 passed**, **89% coverage** (htmlcov generated). Frontend:
  **383 passed**, type-check + lint clean. Backend ruff clean.
- TDD throughout; integration tests run against ephemeral pgvector Postgres via
  testcontainers. Migration chain verified linear `0011 → 0012 → 0013` by the
  up/down/up round-trip test.
- E2E specs collect across the matrix (53 tests / 10 files) and run in CI.

## 1.5 Process

Implement-only cycle via the engineering-team skill, on a plain feature branch
(not a worktree). Five waves, one subagent per unit (or per backend/frontend
split) with strict non-overlapping file boundaries; migration-creating units
serialized (U1=0012 before U2=0013). Lead ran the full backend + frontend suites
at each wave boundary and committed per wave.
