# 1. Roadmap & deferred work

**Status:** active. **Last updated:** 2026-06-28. **Supersedes:** none.

Living list of agreed-but-not-yet-built work and explicitly-deferred ideas, so
they don't get lost between sessions. Most recent context lives in
`journal/260628-general-document-store.md`.

## 1.1 Planned next

1. **Notes (in-app authoring).** Compose/paste text directly into Library
   (title + markdown body) so any copied text becomes a document, with
   in-place editing. Decisions pending — see the session discussion.
2. **Topics ↔ tags refinement.** Resolve the overlap between `topics` and
   `tags` before extending either. The decision gates the "topics in FTS"
   work. See §1.3.
3. **Corpus backfill.** A one-shot command to re-extract / re-embed existing
   documents so they pick up the new prompt (`PROMPT_VERSION`), long-doc
   sampling, `topics`, and structure-preserving markdown chunking. Per-doc
   tasks already exist (`extract_document` / `markdown_document` /
   `embed_document` in `jobs.py`); needs targeting (old prompt version /
   general kinds only), budget-cap respect, and idempotency.
4. **E2E coverage for this session's flows.** Playwright specs for: upload a
   `.md` → reader renders; create a project → assign → filter by it; edit
   `topics`. Unit + integration already cover these; the browser-level
   journeys do not.

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
3. **Document versioning / supersession.** Edited-and-re-uploaded files create
   duplicates (SHA-256 content dedup). Largely mooted for in-app notes if
   those are edited in place (§1.1.1); still relevant for externally-edited
   files re-synced via the consume folder.

## 1.3 Open question — why both `topics` and `tags`?

Both describe doc content, which feels redundant. Working distinction:
`tags` = curated, low-cardinality, **cross-document** labels for
finding/grouping (a filter facet); `topics` = a single document's own
**subject list**, descriptive and meant to be **searchable**, not a filter.
`topics` only earns its place if it becomes search content (FTS); if it stays
a parallel editable taxonomy, it should be dropped in favour of `tags`. To be
decided before extending either.
