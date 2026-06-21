# 1. Ask — semantic question answering

**Status:** active. **Last updated:** 2026-06-16.

Ask lets you put a natural-language question to the archive and get a prose
answer with citations — e.g. *"do I have a travel allowance in my job
contract?"* or *"who was my energy provider last year?"*. It runs in-app
(`/ask` in the web UI, `POST /api/ask` in the REST API); document text never
leaves the host for indexing (local embeddings), and only the final answer
step calls Claude.

## 1.1 The two question classes

Ask handles two different shapes of question, and the answer engine picks the
right tool per question:

1. **Content questions** ("what does this document say?") — e.g. the travel
   allowance clause. Answered by **semantic retrieval**: the question is
   matched against document *contents*, including paraphrase and cross-language
   synonyms (a Dutch "reiskostenvergoeding" clause answers an English "travel
   allowance" question).
2. **Aggregation questions** ("who / how many / how much / over time") — e.g.
   the energy provider. Answered by a **structured query** over the extracted
   metadata columns (`sender`, `kind`, `document_date`, `amount_total`), not by
   reading text.

## 1.2 How it works

```
question ─▶ Claude (tool-use loop) ─┬─▶ semantic_search ─▶ hybrid retrieval ─┐
                                    │                      (FTS + vector RRF) │
                                    └─▶ query_documents ─▶ structured query ──┤
                                                          (sender/kind/date)  │
            answer + citations ◀───── Claude (answers from tool results) ◀────┘
```

1. **Embedding (indexing).** After OCR + extraction + markdown generation,
   each document's text is split into overlapping chunks and embedded with
   **bge-m3** (1024-dim, multilingual) by a local **embedder** sidecar
   (HuggingFace text-embeddings-inference). Vectors are stored in
   `document_chunks` with an HNSW index (cosine). This is a pipeline stage:
   `received → ocr → extract → markdown → embed → indexed`. When a markdown
   layer exists, chunks are drawn from the per-page markdown and each chunk
   carries its `page_number`; without one, chunks come from `ocr_text` with
   `page_number = NULL`. Embedding is best-effort — a document that fails to
   embed still reaches `indexed` and stays searchable by full-text.
2. **Hybrid retrieval** (`semantic_search`). At query time the question is
   embedded and run two ways: vector k-NN over `document_chunks` and the
   existing bilingual Postgres full-text search. The two rankings are fused with
   **Reciprocal Rank Fusion** (RRF, k=60), so exact-term matches (invoice
   numbers, names) and paraphrase matches both surface. Each result carries its
   nearest chunk as the citation excerpt and, when the chunk came from a
   page-aware document, its `page_number`.
3. **Structured query** (`query_documents`). Aggregations over the extracted
   columns: distinct senders, summed amounts (by currency, optionally grouped by
   sender/kind), and document lists. Every row carries the contributing document
   ids for citation. Aggregation citations have no text location, so their
   `page_number` is always `None`.
4. **Answer** (`ask.engine`). Claude (`ask_model`, default
   `claude-sonnet-4-6`) is given the two tools and a bounded number of turns
   (`ask_max_tool_turns`). It is instructed to answer **only** from tool results,
   to say plainly when the archive doesn't contain the answer, and to cite the
   document ids it used. The endpoint returns the answer, the citations
   (document id + title + `page_number`), the tools used, and the estimated cost.
   The web UI renders each citation as `Title, p. N` when a page number is
   available and deep-links the PDF iframe to that page (`#page=N` in the URL
   fragment); citations from documents without a markdown layer show only the
   title.

## 1.3 Configuration

All settings use the `LIBRARY_` env prefix (see `.env.example` /
`src/library/config.py`):

| Setting | Default | Purpose |
|---------|---------|---------|
| `LIBRARY_EMBEDDING_ENABLED` | `true` | Master switch for the embed stage. |
| `LIBRARY_EMBEDDING_SERVICE_URL` | `http://embedder:80` | The bge-m3 sidecar. |
| `LIBRARY_EMBEDDING_MODEL_NAME` | `bge-m3` | Recorded with each embed. |
| `LIBRARY_EMBEDDING_CHUNK_CHARS` | `1800` | Target chunk size. |
| `LIBRARY_EMBEDDING_CHUNK_OVERLAP` | `200` | Overlap carried between chunks. |
| `LIBRARY_RETRIEVE_TOP_K` | `10` | Documents returned by hybrid retrieval. |
| `LIBRARY_ASK_MODEL` | `claude-sonnet-4-6` | Answer model. |
| `LIBRARY_ASK_MAX_TOOL_TURNS` | `4` | Tool-use loop bound. |
| `LIBRARY_ASK_MAX_ANSWER_TOKENS` | `1024` | Max answer length. |

Ask requires `LIBRARY_ANTHROPIC_API_KEY` (the answer step calls Claude); without
it `POST /api/ask` returns `503` and the UI shows a friendly message. Indexing
(embedding) needs only the local sidecar, not the API key.

## 1.4 Cost

The answer step's token cost (and the loop's tool turns) is estimated and
recorded per query in the `ask_logs` table (`query`, `model`, token counts,
`cost_usd`, `used_tools`). Cost is **recorded, not gated** in this release —
ask is interactive and self-limiting. A daily-budget guard mirroring the
extraction budget (`LIBRARY_EXTRACTION_DAILY_BUDGET_USD`) can be added later.
Embedding is local and effectively free.

## 1.5 Operations

- **Backfilling the existing corpus.** Documents indexed before the embed stage
  existed have no chunks. Queue embedding for them with the CLI (the worker must
  be running):

  ```console
  docker compose exec api library backfill-embeddings
  # --limit N to throttle; --include-existing to re-embed everything
  ```

  The job is idempotent (it replaces a document's chunks), so it is safe to
  re-run. On CPU the first run is slow for a large archive — let it work through
  the queue.
- **Deployment** of the embedder sidecar and the pgvector database image:
  see [deployment.md](deployment.md) §1.1 and §1.7.

## 1.6 Limitations (this release)

1. **Page citations are conditional on the markdown layer.** Documents that
   have a `document_pages` row (generated by the `markdown` pipeline stage or
   `backfill-markdown`) carry a `page_number` on their citation. Documents
   ingested before the markdown layer existed, `text/plain` files, and any
   document where the markdown stage was skipped or failed will cite without a
   page number — only the document title is shown.
2. Single-question, single-answer: no multi-turn / conversational follow-up yet.
3. RRF fusion only — no cross-encoder re-ranking.
4. Ask is in-app only; it is not exposed as an MCP tool yet.
5. CPU embedding: the one-time backfill of a large archive is slow.
