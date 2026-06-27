# 1. General document store — extend Library beyond invoices/correspondence

**Date:** 2026-06-28
**Branch:** `eng-general-document-store`

## 1.1 Why

Library was built around financial/correspondence documents — the metadata
model centres on `kind, sender, dates, amounts`. The goal of this work was to
make it a *general* document store that also handles long, born-digital,
project-grouped reference docs, manuals, research papers, and notes — with
search, Ask (RAG), organise-by-project, and structure extraction all working,
and **without degrading the existing invoice/correspondence path**. Driven by
the engineering-team evaluate → plan → develop → wrap-up cycle; the evaluation
found the retrieval half was already general (chunking + pgvector + hybrid RRF)
while the organising/authoring half was financial-shaped end to end.

## 1.2 What shipped (11 work units, W1–W11)

### 1.2.1 Extraction & taxonomy
- New `kind` values `reference`, `research`, `note` (alongside existing
  `manual`) — added in three kept-in-sync places (`KIND_SLUGS`, `KindSlug`
  Literal, kind seed in migration `0010`).
- New `topics: list[str]` metadata field (≤12, human-readable), persisted as
  `documents.topics` JSONB. Extracted for general material; surfaced read/edit
  across REST, MCP, and the detail view (see 1.4).
- Long documents are now **sampled** (head + evenly spaced windows up to ~24k
  chars via `_sample_long_text`) instead of truncated to the first 8k — a
  60-page manual's metadata no longer reflects only its opening pages.
- Prompt reframed from "household paperwork" to a mixed transactional +
  general-reference archive; adaptive summary length; an explicit
  "absence of sender/date/amount is normal, don't guess" instruction.
  `PROMPT_VERSION` bumped.
- `empty_extraction` validation no longer flags clean general docs (those with
  a title/summary but no sender/amount/date) as `needs_review`.

### 1.2.2 Ingestion
- `.md`/`.markdown` detected as `text/markdown` (was opaque `text/plain`);
  added to the upload allow-list and the consume-folder watcher.
- **Born-digital text bypass:** a `text/markdown`/`text/plain` file skips OCR
  and vision-markdown entirely — its raw content becomes a single
  `DocumentPage` markdown layer with **no Claude API call** and no markdown
  budget spend (`_apply_born_digital_markdown`). `page_count` is set to 1.
- Markdown documents embed with a **structure-preserving** chunker
  (`chunk_markdown`, blank-line block boundaries) instead of the
  whitespace-flattening `chunk_text`. Scoped to `text/markdown` only.

### 1.2.3 Projects / collections (new feature)
- First-class many-to-many grouping: `projects` + `document_projects` tables
  (migration `0011`), `Project` model + `Document.projects` relationship,
  `library/projects.py` (`slugify`, `get_or_create_project`, `list_projects`
  with per-project counts excluding soft-deleted docs).
- REST: `GET/POST/GET{slug}/PATCH/DELETE /api/projects`; document membership
  via `PATCH /api/documents/{id}` `projects: string[]` (full-replace,
  upsert-by-name); `GET /api/documents?project=<slug>` filter; `projects` in
  document bodies.
- MCP: `list_projects` tool + a `project` filter on `search_documents`.
- Frontend: a single-select Project filter pill, a comma-separated
  projects editor (datalist of existing projects) in the detail view, and
  read-mode project badges linking to `/?project=<slug>`.

### 1.2.4 Retrieval tuning
- FTS `ts_rank` length normalization (bit 1 = ÷(1+log len)) so a long doc
  no longer dominates a short on-topic invoice by raw match count. Kill
  switch: `FTS_RANK_NORMALIZATION = 0`.
- Ask retrieval now returns **multiple passages per document**
  (`retrieve_chunks_per_doc`, default 3) so a long multi-topic doc can
  contribute several sections; the candidate ranking / anti-crowding stays
  one-chunk-per-doc. Kill switch: `LIBRARY_RETRIEVE_CHUNKS_PER_DOC = 1`.

### 1.2.5 Frontend general-doc UX
- Summary excerpt on list tiles (hidden when a search snippet is shown).
- First-class long-form **reader card** (eager-loaded) replacing the
  collapsed "View markdown" disclosure; a no-PDF `.md`/`.txt` shows the
  reader as its primary pane.
- **Adaptive** hero/metadata groups: in read mode, value-less stats/rows/groups
  hide (no dead em-dashes for general docs); edit mode shows everything so any
  field stays editable.
- Upload accepts `.md`/`.txt` notes.

## 1.3 Key decisions & trade-offs

1. **M2M projects, not a per-doc FK** — a research paper can belong to several
   projects. Mirrors the `tags`/`document_tags` pattern.
2. **Sample long docs rather than raise the front-truncation cap** — gating on
   length (known pre-call) not kind (kind is the extraction *output*), and
   spreading the budget across windows so multi-topic coverage isn't limited to
   the opening pages. Map-reduce over per-page markdown was rejected: at the
   EXTRACT stage `DocumentPage` rows don't exist yet (markdown runs after).
3. **Born-digital markdown bypasses the vision pipeline** — raw content is
   already authoritative, so one synthesized page at zero API cost; this also
   sidesteps the 20-page markdown cap for long `.md`.
4. **Project membership via full-replace `PATCH projects: string[]`** (upsert
   unknown names) rather than dedicated assign/remove endpoints — matches the
   existing free-text tag editing UX, one round-trip.
5. **FTS normalization is hygiene, not the main lever** — whole-doc rank can
   only ever *penalise* length; the real long-doc win is the multi-chunk Ask
   retrieval.

## 1.4 Discovered during wrap-up (code review)

1. **`topics` was write-only** — extracted and stored but exposed in no
   API/MCP response and not editable. Completed it: `topics` now appears in
   document list/detail responses and the MCP document summary, is editable via
   PATCH (full-replace), and renders as badges with an editor in the detail
   view. **Not yet in the FTS tsvector** — deferred (would need a generated-
   column migration + tsvector backfill); topics overlap with summary/tags for
   search in the meantime.
2. **`page_count` was `null` for born-digital markdown** despite one
   `DocumentPage` — fixed in `_apply_born_digital_markdown`.

## 1.5 Tests & verification

- Backend: **585 passed**, **90% coverage** (htmlcov generated). Frontend:
  **369 passed**, type-check + lint clean.
- TDD throughout: every unit added/adjusted tests before code. Integration
  tests run against an ephemeral pgvector Postgres via testcontainers.
- Migration chain verified linear (`0009 → 0010 → 0011`) by the up/down/up
  round-trip test.

## 1.6 Deferred (non-goals this round)

Note authoring / paste-a-note UI and document **versioning** (an edited note
re-uploaded still creates a duplicate via SHA-256 dedup); `.docx`/`.epub`
ingestion (needs a converter dependency); an Ask re-ranker; `topics` in the
FTS index; a dedicated `/projects/:slug` landing route; promoting `topics` onto
the list-item type (currently on the detail type only). Re-extraction /
re-embed backfill of the existing corpus (to pick up the new prompt and
markdown chunking) is an operational step, not code.
