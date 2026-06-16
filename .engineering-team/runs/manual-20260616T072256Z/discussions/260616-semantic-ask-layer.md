# 1. Discussion: semantic "ask the archive" layer

**Date:** 2026-06-16. **Workflow:** engineering-team discussion → build.
**Outcome:** proceed to build the §5 first slice (in-app `/ask`, local embeddings).

## 1.1 Question

Make the library app a flexible, searchable, reliable archive that answers
natural-language questions over its contents — e.g. "do I have a travel
allowance in my job contract?" and "who was my energy provider last year?"

## 1.2 Current state (code-grounded)

1. Pipeline is mature and live on the `paperless` LXC: 5 ingestion channels →
   routed OCR (`nld+eng`) → Claude structured extraction → Postgres FTS → Vue
   PWA + REST + MCP server.
2. Two of three pillars exist: clean full text (`ocr_text`) and structured
   metadata in real columns (`sender`, `kind`, `document_date`,
   `amount_total`, `tags` — `src/library/models.py`).
3. Missing pillar: a semantic-retrieval + reasoning layer. **No embeddings,
   pgvector, or vector search exist** (confirmed by codebase sweep). pgvector
   was reserved "for later" in the inception notes.

## 1.3 Why the two questions fail today

1. **"Travel allowance in my contract?"** — deep read of one document. FTS is
   literal-token; the contract says "reiskostenvergoeding," not "travel
   allowance." Needs semantic retrieval + an answer step that cites the clause.
2. **"Energy provider last year?"** — corpus aggregation. The columns exist
   (`sender`, `kind`, `document_date`) but nothing maps "energy" → a kind, and
   the MCP/API can't run a structured "distinct sender where kind=utility in
   2025" query. Needs a concept→metadata bridge + structured query, not text
   search.

## 1.4 Decisions taken

1. **Ask surface:** in-app `/api/ask` engine + PWA UI (not MCP-only). The whole
   family asks from the app; we own prompt, citations, answer quality.
2. **Privacy/cost:** local multilingual embeddings (`bge-m3` class) so document
   text never leaves the server for indexing; Claude only for the final answer
   step. Near-zero embedding cost.

## 1.5 Target design (first slice)

1. Embedding model: `bge-m3` (multilingual, handles Dutch+English, 1024-dim).
2. Chunk per page (clean citations: "contract, p.3").
3. `document_chunks` table + pgvector + HNSW index.
4. Hybrid retrieval: fuse existing FTS rank with vector similarity (RRF).
5. `/api/ask` → retrieve → reason → answer-with-citations (reuse the existing
   Claude client + cost tracking from extraction).
6. Embedding as a Procrastinate job (incremental post-OCR; throttled backfill
   for the existing corpus).

## 1.6 Open questions carried into planning

1. Embedding serving topology on a 4 vCPU / 4 GB LXC (sidecar container vs
   in-process). Backfill is CPU-heavy and one-time.
2. Whether `/ask` LLM cost is metered under the existing daily budget guard.
3. Whether the structured `query_documents` path (energy-provider class) is in
   the first slice or a fast follow.
