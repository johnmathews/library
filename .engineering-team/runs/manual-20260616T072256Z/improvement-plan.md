---
plan: semantic-ask-layer
units:
  - id: W1
    title: pgvector infra — DB image, extension, document_chunks table + model
  - id: W2
    title: Embedding sidecar service + embedding client
  - id: W3
    title: Chunking + embedding pipeline stage + corpus backfill
  - id: W4
    title: Hybrid retrieval (FTS + vector RRF)
  - id: W5
    title: Structured query path over extracted columns
  - id: W6
    title: /api/ask endpoint with Claude tool-use orchestration
  - id: W7
    title: Frontend Ask view
  - id: W8
    title: Docs, deployment, journal, changelog
---

# 1. Semantic "Ask the archive" — first slice

In-app natural-language Q&A over the document corpus. Local embeddings
(`bge-m3`, 1024-dim) served by a sidecar; Claude only for the final answer
step. `/api/ask` orchestrates two retrieval tools — semantic (hybrid FTS +
vector) and structured (query over extracted columns) — so it answers both
"do I have a travel allowance in my contract?" (content) and "who was my
energy provider last year?" (aggregation).

Grounding for every change below is in the two Phase-2 research reports
(file:line citations there); key anchors repeated inline.

## 1.1 Decisions & key choices

1. **Embedding topology: dedicated sidecar container.** HuggingFace
   text-embeddings-inference (TEI) CPU image serving `BAAI/bge-m3`. One model
   copy, memory-capped; both `api` (query-time) and `worker` (indexing) call it
   over HTTP. No model deps in the app image. Requires the LXC bumped to
   ~6-8 GB RAM (TEI + bge-m3 ≈ 2.5-3 GB resident) — flagged in W8.
2. **Answer orchestration: Claude tool-use loop.** `/api/ask` runs a short
   agent loop where Claude is given two tools — `semantic_search` (W4) and
   `query_documents` (W5) — chooses which to call, then answers strictly from
   the returned context with citations. This cleanly handles both content and
   aggregation questions without a brittle hand-rolled router. Fallback if it
   proves flaky: a classifier that picks one path (noted in W6).
3. **Cost: recorded, not gated.** Each ask records an `ask_completed`
   `IngestionEvent` with token cost (reuse `estimate_cost_usd`), but no daily
   budget gate in this slice (per decision). A `LIBRARY_ASK_DAILY_BUDGET_USD`
   guard mirroring extraction can be added later.
4. **Chunking: per page.** Page-level chunks give clean citations
   ("contract, p.3") and fit bge-m3's 8k context. OCR text is page-delimited
   where available; otherwise the whole doc is one chunk.
5. **Fusion: Reciprocal Rank Fusion (RRF).** Combine the existing FTS rank with
   vector cosine rank by `Σ 1/(k + rank_i)`, k=60. No cross-encoder re-rank in
   this slice.

## 1.2 Non-goals

1. Not exposing `ask` through the MCP server — in-app only this slice (the MCP
   client can already compose search+read).
2. Not building the facts/knowledge-graph or entity/timeline features (the
   discussion's §3.4/3.5) — later enrichment.
3. Not adding a hard daily-budget gate on ask (cost recorded only).
4. Not cross-encoder re-ranking, query expansion, or multi-turn/conversational
   ask — single question → single cited answer.
5. Not GPU inference — CPU only for bge-m3.

## 1.3 Ordering rationale

Foundation-first then risk-first: schema/infra (W1) and the embedder (W2)
unblock everything; the indexing pipeline (W3) must exist before retrieval has
data; retrieval paths (W4, W5) are independent and feed the endpoint (W6);
frontend (W7) consumes the settled API contract; docs/deploy (W8) last so they
describe the shipped shape.

# 2. Work units

## 2.1 W1 — pgvector infra: DB image, extension, document_chunks table + model

- **ID:** W1
- **Priority:** High
- **Risk:** Medium (DB image change + schema migration on a live prod DB)
- **Size:** M
- **Changes:**
  1. `docker-compose.yml`: swap `db` image `postgres:17.5-alpine` →
     `pgvector/pgvector:pg17` (drop-in; note: not alpine — confirm volume
     compatibility, same PGDATA layout).
  2. `pyproject.toml`: add `pgvector` Python package (SQLAlchemy `Vector` type).
  3. `src/library/models.py`: add `DocumentChunk` model — `id` BigInteger PK,
     `document_id` FK→documents ON DELETE CASCADE, `page` Integer, `text` Text,
     `embedding Vector(1024)`, `created_at`. Add `chunks` relationship to
     `Document` (cascade all/delete-orphan, lazy selectin), mirroring the
     `events` relationship at models.py:~270.
  4. New migration `migrations/versions/0004_add_document_chunks.py` (follow
     0003 style): `CREATE EXTENSION IF NOT EXISTS vector`; create
     `document_chunks`; HNSW index on `embedding`
     (`postgresql_using="hnsw"`, `{m:16, ef_construction:200}`,
     `vector_cosine_ops`); btree index on `document_id`. Symmetric `downgrade`.
- **Test impact:** Add `tests/test_models.py` cases for DocumentChunk + cascade
  delete. Add a migration smoke test (upgrade→downgrade) if one exists for 0001-3;
  read `tests/` for the existing migration-test pattern before writing.
  Existing model tests: none broken (additive).
- **Reversibility:** Down-migration drops table + extension. Image swap reverts
  by restoring the old tag (data volume unaffected — pgvector pg17 reads the
  same PGDATA). Back up `pgdata` before first prod upgrade.
- **Dependencies:** none.
- **Acceptance criteria:** `alembic upgrade head` then `downgrade -1` both
  succeed on a pgvector container; `DocumentChunk` round-trips a 1024-d vector
  in a test; deleting a Document cascades its chunks.

## 2.2 W2 — Embedding sidecar service + embedding client

- **ID:** W2
- **Priority:** High
- **Risk:** Medium (new infra; LXC memory pressure)
- **Size:** M
- **Changes:**
  1. `docker-compose.yml`: add `embedder` service using
     `ghcr.io/huggingface/text-embeddings-inference:cpu-<pinned>`, `--model-id
     BAAI/bge-m3`, model cache volume, `mem_limit` ~3g, healthcheck on its
     `/health`. `api` and `worker` get `depends_on: embedder` and an
     `LIBRARY_EMBEDDING_SERVICE_URL` env.
  2. `src/library/config.py`: add settings `embedding_service_url`
     (default `http://embedder:80`), `embedding_model_name` (`bge-m3`),
     `embedding_dim` (1024), `retrieve_top_k` (10) — mirror the existing
     `BaseSettings`/`env_prefix="LIBRARY_"` pattern (config.py:~14,38).
  3. New `src/library/embedding/client.py`: async `embed_texts(list[str]) ->
     list[list[float]]` and `embed_query(str)` calling TEI `/embed` via httpx;
     batching, timeout, retry; normalize to unit length if TEI doesn't.
- **Test impact:** New `tests/test_embedding_client.py` — mock the TEI HTTP
  endpoint (respx/httpx mock), assert batching, error handling, dim==1024.
  No existing tests affected.
- **Reversibility:** Pure-additive code + one compose service; revert commit and
  remove the service. No data migration.
- **Dependencies:** none (parallelizable with W1).
- **Acceptance criteria:** With the embedder running, `embed_query("test")`
  returns a 1024-d unit vector; client unit tests green against a mocked TEI.

## 2.3 W3 — Chunking + embedding pipeline stage + corpus backfill

- **ID:** W3
- **Priority:** High
- **Risk:** Medium (touches the document pipeline hot path)
- **Size:** L (consider splitting backfill into its own commit; not a separate unit)
- **Changes:**
  1. `src/library/models.py`: add `DocumentStatus.EMBED` between `EXTRACT` and
     `INDEXED`.
  2. `jobs.py`: extend `_NEXT_STATUS` (jobs.py:26) EXTRACT→EMBED→INDEXED; add
     `run_embed(session, document)` and an `elif status is DocumentStatus.EMBED`
     branch in `_run_stage_hook` (jobs.py:116). `run_embed` deletes any existing
     chunks for the doc (idempotent re-embed), splits `ocr_text` into per-page
     chunks, calls `embed_texts`, inserts `DocumentChunk` rows, records an
     `embedded` `IngestionEvent`. Never fail the pipeline — mirror the extraction
     invariant (skip + audit event on error, document still reaches INDEXED).
  3. New `src/library/embedding/chunker.py`: page-splitting helper (use
     page-delimiter if OCR stores one; else single chunk). Cap chunk size.
  4. Backfill: a CLI entry (extend `scripts/` or a Procrastinate one-off task)
     that enqueues re-embed for all INDEXED docs lacking chunks, throttled.
- **Test impact:** `tests/test_jobs.py` (or equiv) — add EMBED-stage test: a doc
  with OCR text produces N chunks with vectors and reaches INDEXED; error path
  skips and still indexes. Existing pipeline tests that assert
  EXTRACT→INDEXED transition **will need updating** to expect the new EMBED
  stage — read the current pipeline test before editing. Chunker unit tests.
- **Reversibility:** Revert commit; new docs simply skip embedding. Existing
  chunks can be dropped via W1 down-migration. Backfill is re-runnable and
  idempotent (deletes-then-inserts per doc).
- **Dependencies:** W1 (table), W2 (embedding client).
- **Acceptance criteria:** Ingesting a multi-page PDF yields one chunk per page
  with 1024-d vectors and status INDEXED; backfill populates chunks for a
  pre-existing doc; embedder-down leaves the doc INDEXED with an audit event.

## 2.4 W4 — Hybrid retrieval (FTS + vector RRF)

- **ID:** W4
- **Priority:** High
- **Risk:** Low-Medium
- **Size:** M
- **Changes:**
  1. `src/library/search.py`: add `async def semantic_search(session, query,
     filters, top_k)` — embed the query (W2), run a vector kNN over
     `document_chunks` (`embedding.cosine_distance(qvec)`), run the existing FTS
     query (reuse `build_document_query`, search.py:100), fuse by RRF (k=60) to a
     ranked list of documents, each with its best-matching chunk(s) as
     `{document_id, page, text}` for citations. Respect existing
     `DocumentFilters` (kind/sender/tag/date/language).
  2. Keep the current keyword `build_document_query` untouched for the existing
     `/api/documents?q=` search.
- **Test impact:** New `tests/test_semantic_search.py` — seed docs+chunks with
  known vectors (monkeypatch the embedder), assert RRF ordering and that filters
  apply. Existing `search.py` tests unaffected (new function).
- **Reversibility:** Pure additive code; revert commit.
- **Dependencies:** W1 (chunks), W2 (query embedding). Soft: easier after W3 has
  real data, but testable with seeded chunks.
- **Acceptance criteria:** A paraphrase query ("travel allowance") ranks the
  chunk containing the synonym ("reiskostenvergoeding") above unrelated docs;
  date/kind filters narrow results.

## 2.5 W5 — Structured query path over extracted columns

- **ID:** W5
- **Priority:** High
- **Risk:** Low
- **Size:** M
- **Changes:**
  1. New `src/library/structured_query.py`: `async def query_documents(session,
     *, kind=None, sender_contains=None, date_from=None, date_to=None,
     aggregate=None)` over `documents` columns (`sender`, `kind`,
     `document_date`, `amount_total`, `currency`). Supports list + simple
     aggregates: distinct senders, count, sum(amount_total) grouped by
     sender/kind/period. Returns compact rows + the source document ids for
     citation.
  2. A small concept→kind hint map (e.g. "energy"/"utility"→`utility-bill`)
     surfaced to the LLM as tool description text, not hardcoded routing.
- **Test impact:** New `tests/test_structured_query.py` — seed docs across
  senders/kinds/dates, assert distinct-sender-in-range and sum aggregates. No
  existing tests affected.
- **Reversibility:** Pure additive; revert commit.
- **Dependencies:** none hard (uses existing columns). Feeds W6.
- **Acceptance criteria:** "distinct sender where kind=utility-bill in 2025"
  returns the right sender(s) with their document ids; a sum aggregate matches a
  hand-computed total in the test fixture.

## 2.6 W6 — /api/ask endpoint with Claude tool-use orchestration

- **ID:** W6
- **Priority:** High
- **Risk:** Medium (new authenticated endpoint; LLM cost; orchestration)
- **Size:** L
- **Changes:**
  1. New `src/library/api/ask.py`: `POST /ask` with `AskRequest{query, top_k?}`
     and `AskResponse{answer, citations:[{document_id,title,page,snippet}],
     used_tools, cost_usd}`. Auth via the existing `current_user` gate (it's
     mounted under the authed `/api` router).
  2. New `src/library/ask/engine.py`: Claude tool-use loop. Instantiate
     `AsyncAnthropic` (apply.py:179 pattern); expose two tools — `semantic_search`
     (W4) and `query_documents` (W5); system prompt: answer **only** from tool
     results, cite each claim by document id+page, say plainly when the archive
     doesn't contain the answer. Bounded loop (max ~4 tool turns). Compute cost
     with `estimate_cost_usd` (extractor.py:124) summed across turns; record an
     `ask_completed` `IngestionEvent` (no document_id — or a nullable variant;
     check the events schema and adapt) with `{query, model, cost_usd,
     used_tools, citations}`. Config: `ask_model` (default `claude-sonnet-4-6`),
     add to `MODEL_PRICING_USD_PER_MTOK` if not present.
  3. `src/library/app.py`: `api_router.include_router(ask.router)` (app.py:133).
- **Test impact:** New `tests/test_api_ask.py` — mock Anthropic (canned
  tool_use → tool_result → final answer) and the embedder; assert citations are
  well-formed, a cost event is written, and "not in archive" answers when
  retrieval is empty. Verify the `IngestionEvent` schema allows a
  document-less/ask event (may need a nullable `document_id` or a separate
  audit row — decide in W6, note any migration needed back to W1).
- **Reversibility:** Revert commit removes the route; no schema change unless the
  ask-event needs a nullable column (call out + fold into W1's migration if so).
- **Dependencies:** W4, W5, W2.
- **Acceptance criteria:** Asking "do I have a travel allowance in my contract?"
  against a seeded contract returns a cited answer pointing at the right page;
  "who was my energy provider last year?" routes through `query_documents` and
  names the sender; empty corpus yields an honest "not found".

## 2.7 W7 — Frontend Ask view

- **ID:** W7
- **Priority:** Medium
- **Risk:** Low
- **Size:** M
- **Changes:**
  1. New `frontend/src/api/ask.ts`: `askQuestion(question, signal)` via the
     existing `apiFetch` wrapper (client.ts) — POST `/api/ask`, typed
     `AskResponse`.
  2. New `frontend/src/views/AskView.vue`: question input (AppInput/AppButton),
     loading state, answer block, citation cards reusing `renderSnippet`
     (utils/snippet.ts) and linking to `document-detail`. Match GOV.UK/Tailwind
     conventions from `DocumentListView.vue`.
  3. `frontend/src/router/index.ts`: add `/ask` route (lazy import). Add a nav
     entry/affordance (and optionally an "Ask" action in `SearchModal.vue`).
- **Test impact:** New `frontend/src/views/__tests__/AskView.spec.ts` (vitest) —
  mock `askQuestion`, assert answer + citation rendering and the empty/loading
  states. Optional Playwright e2e happy-path. No existing specs affected.
- **Reversibility:** Revert commit; route disappears.
- **Dependencies:** W6 (API contract).
- **Acceptance criteria:** Typing a question and submitting shows the answer and
  clickable citations; vitest spec green; e2e (if added) passes on Chromium.

## 2.8 W8 — Docs, deployment, journal, changelog

- **ID:** W8
- **Priority:** Medium
- **Risk:** Low
- **Size:** M
- **Changes:**
  1. New `docs/ask.md`: the ask feature — architecture (embedder, chunks, hybrid
     retrieval, tool-use loop), the two question classes, config/env, cost
     behaviour. Numbered headings.
  2. Update `docs/architecture.md` (add EMBED stage + ask flow), `docs/api.md`
     (`POST /api/ask`), `docs/deployment.md` (pgvector image swap; new `embedder`
     service; **bump LXC to ~6-8 GB RAM**; backfill step; pgdata backup-before-
     upgrade note), `docs/ingestion.md` if it lists pipeline stages.
  3. New `journal/2606xx-semantic-ask-layer.md` entry; `CHANGELOG.md` under a
     new Unreleased/0.2.0 section.
- **Test impact:** None (docs). Verify any code snippet in docs matches shipped
  signatures.
- **Reversibility:** n/a.
- **Dependencies:** W1-W7 (describes shipped behaviour).
- **Acceptance criteria:** `docs/ask.md` exists and accurately describes the
  shipped endpoint + topology; deployment.md tells an operator exactly what to
  change (image, service, RAM) to deploy; CHANGELOG updated.
