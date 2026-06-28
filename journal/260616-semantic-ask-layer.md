# Semantic "Ask the archive" layer

**Date:** 2026-06-16. **Branch:** `worktree-eng-semantic-ask-layer`.
**Workflow:** engineering-team discussion → build (W1–W8).

## 1.1 What and why

Turned the archive from "searchable" into "askable". You can now put a
natural-language question to it — *"do I have a travel allowance in my job
contract?"*, *"who was my energy provider last year?"* — and get a prose answer
with citations, in-app at `/ask` and via `POST /api/ask`. Design discussion and
decisions are captured in
`.engineering-team/runs/manual-20260616T072256Z/discussions/260616-semantic-ask-layer.md`;
the user-facing guide is [docs/ask.md](../docs/ask.md).

The starting point already had two of the three pillars — clean OCR text and
structured extracted metadata (`sender`, `kind`, `document_date`,
`amount_total`). What was missing was a semantic-retrieval + reasoning layer.
The two example questions stress different gaps: the contract question needs
*content* retrieval with paraphrase/cross-language matching; the energy-provider
question needs *aggregation* over the metadata columns. We built both.

## 1.2 Key decisions

1. **In-app answer engine**, not MCP-only: the whole family asks from the app;
   we own the prompt, citations, and answer quality.
2. **Local embeddings** (`bge-m3` via a text-embeddings-inference sidecar) so
   document text never leaves the host for indexing; Claude only for the final
   answer step. Multilingual model chosen for the Dutch+English corpus.
3. **Bigger first slice**: semantic *and* structured retrieval, so both example
   questions work now.
4. **Ask cost recorded, not gated** (in `ask_logs`); ask is interactive and
   self-limiting.
5. **Answer orchestration = a Claude tool-use loop** with two tools
   (`semantic_search`, `query_documents`); Claude picks the path. Cleaner and
   more robust than a hand-rolled router.

## 1.3 What shipped (W1–W8)

1. **W1** — pgvector: `db` image → `pgvector/pgvector:pg17`, `document_chunks`
   table + HNSW (cosine) index, `DocumentChunk` model, migration 0004.
2. **W2** — `embedder` sidecar (TEI serving bge-m3) + async embedding client
   (batching, dim validation).
3. **W3** — new `EMBED` pipeline stage (`received → ocr → extract → embed →
   indexed`), word-window chunker with overlap, `embed_document` job +
   `library backfill-embeddings` CLI. Embedding is best-effort: a document that
   fails to embed still reaches `indexed`.
4. **W4** — hybrid retrieval: bilingual FTS fused with vector cosine k-NN via
   Reciprocal Rank Fusion (`semantic_search`).
5. **W5** — structured query path (`query_documents`): distinct senders, summed
   amounts by currency/sender/kind, document lists; each row carries citable
   document ids.
6. **W6** — `POST /api/ask` + the tool-use engine; `ask_logs` table (migration
   0005) records cost/provenance.
7. **W7** — frontend `AskView` (cited-answer cards), `/ask` route, sidebar link.
8. **W8** — docs (new `ask.md`; architecture/api/deployment/`.env.example`/
   CHANGELOG updates).

Final state: 337 backend tests (89% coverage), 232 frontend tests, lint clean.

## 1.4 Notable discoveries and gotchas

1. **Postgres collation changed with the DB image.** Swapping
   `postgres:17.5-alpine` (musl, C-collation) for `pgvector/pgvector:pg17`
   (Debian/glibc) flipped text ordering to a linguistic collation and broke two
   taxonomy ordering tests. Fix: pin the new image to `C.UTF-8`
   (`POSTGRES_INITDB_ARGS=--locale=C.UTF-8`) — byte-order, libc-independent,
   matches the existing cluster. This also makes **reusing the existing
   `pgdata` volume safe** (C collation can't suffer a glibc-vs-musl
   collation-version index mismatch). Documented in deployment.md §1.7.1.
2. **No CHECK constraint on `documents.status`.** SQLAlchemy 2.0
   `Enum(native_enum=False)` defaults to `create_constraint=False`, so adding
   `DocumentStatus.EMBED` needed *no* constraint migration — just the Python
   enum value (VARCHAR(16) fits "embed"). A code-review pass flagged a
   (non-existent) constraint as a critical bug; verified false via DB
   introspection and the passing `embed → indexed` integration test.
3. **OCR text has no reliable page boundaries** (PDF pages join with `\n\n`,
   like paragraphs). So chunks are word-windows and the citation ordinal is
   `chunk_index`, not a PDF page — named honestly rather than over-claiming.
4. **Test hermeticity**: embeddings default *off* in tests (autouse fixture);
   tests that exercise them opt in and monkeypatch the embed call, so the suite
   never reaches for the network sidecar.

## 1.5 Code-review fixes applied

A reviewer pass produced 9 findings; the material ones were fixed:
1. Vector candidate pool now counts *documents* (`DISTINCT ON (document_id)`),
   so a many-chunk document can't crowd the pool (+regression test).
2. `/ask` citations now prefer the documents Claude actually cited inline
   (`#id`), falling back to the full retrieved set.
3. Guarded the tool-use loop against sending an empty user turn (Anthropic 400)
   when `stop_reason=tool_use` yields no tool_use blocks.
4. Migration 0004 downgrade no longer drops the `vector` extension (safer on
   shared servers).
Rejected the "critical" #1 (see 1.4.2). Accepted minors (array_agg ordering,
`used_tools` JSON shape) as cosmetic.

## 1.6 Operational notes for deploying this

1. **LXC RAM**: bump to ~6–8 GB (the embedder needs ~3 GB). Or set
   `LIBRARY_EMBEDDING_ENABLED=false` and drop the `embedder` service to stay at
   ~4 GB (no semantic Ask).
2. **Upgrade path** (deployment.md §1.7.1): back up `pgdata`, `compose up`
   (image swap is safe in place), then `library backfill-embeddings` to index
   the existing corpus.
3. **CI**: the `e2e` and `compose-smoke` jobs now pull the TEI image and start
   the embedder (api/worker `depends_on` it, `service_started`); they only wait
   on api/worker/db health, so the model download doesn't gate them. Slightly
   heavier CI for full-stack fidelity.

## 1.7 Deliberately out of scope (follow-ups)

Conversational/multi-turn ask; cross-encoder re-ranking; exposing ask as an MCP
tool; a daily-budget gate on ask cost; atomic "facts" extraction and an
entity/timeline view (the discussion's §3.4/3.5).

## 1.8 Production deployment (same day)

Deployed to the live `paperless` LXC the same session. The live stack is a
custom compose at `/srv/apps/docker-compose.yml` (co-hosting paperless-ngx),
**not** the repo's — runbook now in [docs/deployment.md](../docs/deployment.md)
§1.7.2. Procedure: `pg_dump -Fc` backup → edit compose (db image, embedder
service, env/depends_on) → `compose pull && up -d` (migrate applied 0004/0005)
→ `library backfill-embeddings` (105 docs, 377 chunks, ~6 min on CPU). Verified
end-to-end: "do I have a travel allowance in my contract?" correctly found and
quoted the (Dutch) contract clause for an English question.

Three deployment gotchas — all now in the runbook + the deployment-topology memory:
1. **glibc collation mismatch.** The old `postgres:17` was a *newer* glibc
   (2.41) than `pgvector/pgvector:pg17` (2.36), so reusing pgdata logged a
   collation-version mismatch (stale text-index sort order). Fixed with
   `REINDEX DATABASE` + `ALTER DATABASE … REFRESH COLLATION VERSION`. (The
   §1.7.1 draft had wrongly claimed "safe in place" on a musl→glibc assumption
   that didn't hold for this instance.)
2. **Embedder OOM.** TEI's default `--max-batch-tokens 16384` warmup OOM-killed
   bge-m3 even at 4 GB (17 restart loops). Fixed with `--max-batch-tokens 2048`
   + `mem_limit: 6g` — we embed page-sized chunks, so 2048 is plenty.
3. **API key placement.** `/ask` answering (and Claude extraction) needs
   `LIBRARY_ANTHROPIC_API_KEY`, which was unset. `.env` is regenerated
   externally (Portainer), so the key had to go in the compose `environment:`
   block of `library-webserver` + `library-worker`, not `.env`.

Also closed four doc-accuracy gaps the build's W8 pass missed: three stale
`received→ocr→extract→indexed` lifecycle references (api.md status enum,
ingestion.md diagram + transitions) that predate the `embed` stage, and the
`AskView` in frontend.md.
