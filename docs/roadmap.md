# 1. Roadmap & deferred work

**Status:** active. **Last updated:** 2026-06-28. **Supersedes:** none.

Living list of agreed-but-not-yet-built work and explicitly-deferred ideas, so
they don't get lost between sessions. Most recent context lives in
`journal/260628-general-document-store.md`.

## 1.1 Planned next

Nothing is currently queued.

The **admin role + admin views** shipped in the 2026-06-28 cycle: a boolean
`users.is_admin` role with a `require_admin` guard, global project mutations
gated to admins, the `/api/admin/*` API (system/architecture/coverage/users),
the `/admin` views page, and a CI coverage-summary pipeline baked into the
image. See [admin.md](admin.md).

The previously-planned items all shipped in the 2026-06-28 notes + topics
refinement cycle:

- **Notes (in-app authoring)** — `DocumentSource.NOTE`, the `/api/notes` router
  (create / edit-in-place / version history / restore) and the New-note + detail
  editing UI. See [api.md §1.16](api.md), [ingestion.md](ingestion.md) "Notes",
  and [frontend.md](frontend.md).
- **Topics ↔ tags refinement** — decided (see §1.3): `topics` is now read-only
  and folded into full-text search; `tags` stays the editable filter facet.
- **Corpus backfill** — the `library backfill` command (re-enqueues
  extract→markdown→embed for documents on a stale `PROMPT_VERSION`, general-only
  by default, budget-cap respected worker-side). See
  [ingestion.md](ingestion.md) "Backfill (stale prompt version)".
- **E2E coverage** — Playwright specs `markdown-reader`, `projects`, `notes`,
  and `topics-readonly` (run in CI's e2e job). See [frontend.md §1.7](frontend.md).

## 1.2 Deferred — implement only if a trigger fires

1. **Ask re-ranker.** A second relevance-scoring pass (cross-encoder or
   LLM-judge) over retrieved candidates before they reach Claude.
   **Not needed today** — hybrid retrieval (vector + FTS, RRF-fused) plus
   multi-passage-per-doc (`LIBRARY_RETRIEVE_CHUNKS_PER_DOC`) is sufficient at
   personal scale (`top_k ≤ 10`, single user). **Trigger to revisit:** if Ask
   answers are visibly missing or misranking relevant passages that *are* in
   the corpus — i.e. retrieval quality, not generation, is the bottleneck.
   Cheapest first step then is an LLM-judge rerank reusing the existing
   Anthropic client; only stand up a dedicated reranker model if that's
   inadequate.
2. **`.docx` / `.epub` ingestion.** Not wanted — corpus is PDF / `.md` /
   `.txt` only.
3. **Document versioning / supersession (non-note files).** Edited-and-re-uploaded
   files create duplicates (SHA-256 content dedup). Now **mooted for in-app notes**
   — they are edited in place with their own `note_versions` history and bypass
   the content dedup (salted sha) — but still relevant for externally-edited
   files re-synced via the consume folder.

## 1.3 Decided — `topics` vs `tags`

**Resolved (2026-06-28).** The two no longer overlap:

- `tags` = curated, low-cardinality, **cross-document** labels for
  finding/grouping — the editable **filter facet** (`PATCH /api/documents/{id}`,
  the `?tag=` filter).
- `topics` = a single document's own auto-extracted **subject list** — now
  **read-only** (removed from `DocumentUpdate` and the detail editor) and folded
  into the full-text search vectors (`search_vector_nl`/`search_vector_en` via
  `coalesce(topics::text,'')`, migration `0012_topics_fts`). It renders as
  read-only badges in the UI and still appears in list/detail REST responses and
  the MCP document summary.

`topics` earned its place by becoming search content rather than a parallel
editable taxonomy.
