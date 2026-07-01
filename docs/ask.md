# Ask — semantic question answering

**Status:** active. **Last updated:** 2026-06-29 (recipient in answer context; citations collapsed by default; empty-state distinguishes no-conversations from none-selected).

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
3. **Comparative questions** ("is this bill higher than usual?", "how does it
   compare to last year?", "are my bills going up?") — answered by the series
   engine against the statistical distribution of recurring documents from the
   same sender and kind.

## 1.2 How it works

```
question ─▶ Claude (tool-use loop) ─┬─▶ semantic_search ─▶ hybrid retrieval ─┐
                                    │                      (FTS + vector RRF) │
                                    ├─▶ query_documents ─▶ structured query ──┤
                                    │                     (sender/kind/date)  │
                                    └─▶ compare_to_series ─▶ series stats ───┤
                                                            (distribution/    │
                                                             trend/YoY)       │
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
   page-aware document, its `page_number`. Each result also exposes the
   document's metadata to the model (title, **sender**, **recipient**,
   document date) so it can attribute and group answers. The FTS leg uses `ts_rank`
   **length normalization** (Postgres bitmask `1`: divide rank by `1 + log(length)`),
   so a long, multi-topic document cannot out-rank a short on-topic invoice
   merely by repeating the matched term — score reflects match *density*. For
   long documents, Ask retrieval also pulls the `LIBRARY_RETRIEVE_CHUNKS_PER_DOC`
   nearest chunks per result (best-first) and joins them into the excerpt with a
   `[…]` separator, so multi-topic answers see more than the single best passage.
   The per-document candidate ranking and anti-crowding guarantee are unchanged
   (one chunk per document still drives fusion).
3. **Structured query** (`query_documents`). Aggregations over the extracted
   columns: distinct senders, summed amounts (by currency, optionally grouped by
   sender/kind), and document lists. Every row carries the contributing document
   ids for citation; document refs also expose `title`, `sender`, `recipient`,
   `kind`, `document_date`, and `amount_total`. Aggregation citations have no text location, so their
   `page_number` is always `None`.

   **Quotes are not expenditure.** Documents of kind `quote` (estimates not yet
   incurred) are **excluded from `sum_amount` spend totals by default**, so a
   question like "what have I spent in the last 3 months?" ignores quotes. To
   total quotes specifically, the model passes `kind="quote"` (also surfaced via
   the concept→kind hints `quote`/`estimate` → `quote`). The exclusion lives in
   `structured_query.sum_amount`, not the prompt, so it holds regardless of how
   the question is phrased.
4. **Series comparison** (`compare_to_series`). Statistical summary of a
   recurring-document series — see §1.7 for details. Returns distribution
   (count/mean/median/stdev/min/max), a reference-vs-usual verdict, a trend
   direction, and a year-over-year comparison. All members contribute their ids
   to the citation set.
5. **Answer** (`ask.engine`). Claude (`ask_model`, default
   `claude-opus-4-8`) is given the three tools and a bounded number of turns
   (`ask_max_tool_turns`). It is instructed to answer **only** from tool results,
   to say plainly when the archive doesn't contain the answer, and to cite the
   document ids it used. The endpoint returns the answer, the citations
   (document id + title + `page_number`), the tools used, and the estimated cost.
   The web UI **collapses the citations by default** behind an `AppDetails`
   disclosure ("Citations (N)") under each answer, and renders each citation as
   `Title, p. N` when a page number is available and deep-links the PDF iframe to
   that page (`#page=N` in the URL fragment); citations from documents without a
   markdown layer show only the title.

**Image attachments (W11).** `ask_model` (`claude-opus-4-8`) is multimodal, so
a question may carry up to 5 base64 images (see [api.md §1.11](api.md)). They are
rendered as image content blocks on the question turn alongside the text, and the
system prompt tells the model to read them as evidence and combine them with tool
results. Attachments persist in `ask_turns.messages`, so they replay as history on
follow-ups. The composer offers an **Attach image** control with preview + remove.

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
| `LIBRARY_RETRIEVE_CHUNKS_PER_DOC` | `3` | Nearest passages per document folded into the Ask excerpt (best-first, `[…]`-joined); `1` = legacy single-chunk. Does not affect candidate ranking or citations. |
| `LIBRARY_ASK_MODEL` | `claude-opus-4-8` | Answer model. |
| `LIBRARY_ASK_MAX_TOOL_TURNS` | `4` | Tool-use loop bound per turn. |
| `LIBRARY_ASK_MAX_ANSWER_TOKENS` | `1024` | Max answer length. |
| `LIBRARY_ASK_HISTORY_TURNS` | `3` | Prior turns re-fed into the loop for follow-ups; `0` disables history (each turn answered cold, still recorded). |
| `LIBRARY_SERIES_MIN_DOCUMENTS` | `3` | Minimum members before series stats are reported; below this `status:"insufficient"` is returned. |
| `LIBRARY_SERIES_TYPICAL_PCT` | `0.10` | Half-width of the "typical" verdict band as a fraction of the median (OR'd with ±1 stdev). |
| `LIBRARY_SERIES_FLAT_PCT` | `0.05` | First→last change fraction at or below which the trend direction is reported as `flat`. |

Ask requires `LIBRARY_ANTHROPIC_API_KEY` (the answer step calls Claude); without
it `POST /api/ask` returns `503` and the UI shows a friendly message. Indexing
(embedding) needs only the local sidecar, not the API key.

## 1.4 Cost

The answer step's token cost is estimated and recorded per turn in the
`ask_turns` table (`query`, `answer`, `model`, token counts, `cost_usd`,
`used_tools`). The total cost of a conversation thread is the sum of its turns'
`cost_usd`. Cost is **recorded, not gated** in this release — Ask is
interactive and self-limiting. A daily-budget guard mirroring the extraction
budget (`LIBRARY_EXTRACTION_DAILY_BUDGET_USD`) can be added later. Embedding
is local and effectively free.

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

## 1.6 Conversational Ask

Ask is multi-turn. A follow-up like *"what about last year?"* resolves against
the prior turns of the same conversation rather than being answered cold.

### How it works

Each question is a **turn** within a persistent **thread**. Threads are stored
server-side in two tables: `ask_threads` (one conversation, with a title and
owner) and `ask_turns` (one Q&A turn, recording the question, answer, citations,
token cost, and the full serialized Anthropic message blocks this turn produced).

When a follow-up arrives the engine loads the last `LIBRARY_ASK_HISTORY_TURNS`
turns (default 3) from the database, concatenates their serialized message
blocks into a history prefix, and prepends that prefix to the current question
before calling Claude. This means Claude can reason over earlier tool results
without re-querying — the faithful replay path.

**Prompt caching.** When a history prefix is present, the engine marks the
boundary of the rehydrated prefix with an Anthropic `cache_control: ephemeral`
breakpoint, and the static system prompt/tool definitions also carry a breakpoint.
Re-sent turns hit the Anthropic prompt cache, reducing cost and latency on
follow-ups.

**Sliding window trade-off.** Older turns are dropped when a thread exceeds
`LIBRARY_ASK_HISTORY_TURNS`. Dropped turns cause the history-prefix cache to
miss (the cache key changes when earlier turns fall off), while the static
system+tools prefix stays cached. Most threads are short; this is an accepted
trade-off for bounded token usage.

### Using threads via the API

```
POST /api/ask        {"question": "..."}                     → creates a new thread
POST /api/ask        {"question": "...", "thread_id": 42}    → continues thread 42
GET  /api/ask/threads                                        → list your conversations
GET  /api/ask/threads/42                                     → thread detail + all turns
DELETE /api/ask/threads/42                                   → delete a conversation
```

See [api.md](api.md) §1.11 for the full wire contract.

### Web UI

The Ask view (`/ask`) is a chat interface: the page title and description sit
full-width at the top, with the working area below — a conversation sidebar
listing past threads (by title and relative time) with resume and delete
actions, a scrollable transcript of Q&A pairs, and a follow-up input pinned
below. At lg+ the transcript scrolls internally (the header and composer stay
put) and both the transcript and the thread list show a subtle thin scrollbar
(`.thin-scrollbar`) so the scroll region reads as independently scrollable —
rather than hiding the bar, which removed that affordance. On a phone the sidebar stacks beneath the title/description; on wide
screens it sits beside the answer column. `/ask/:threadId` loads an existing
thread. **"New conversation"** clears to an empty thread; when the view is already
an empty new conversation (no thread selected, no turns) the button is greyed out
and disabled, since starting a new one there would do nothing.

On a **fresh `/ask`** the view reads a **`?q=` query parameter** on initial mount:
when present (and not resuming a `/ask/:threadId` thread) it seeds the composer
textarea with that text and opens/focuses the composer, ready to send or edit.
This is what the document detail view's **"Ask about this document"** button uses
(see [frontend.md §1.5](frontend.md)) — it links to `/ask?q=<prompt>` in a new
tab with a prompt naming the document. It is **pre-fill only**: there is no
backend change and no document scoping — the named document is surfaced by the
ordinary Ask retrieval.

Each turn is visually layered so the panel, the question, and the answer are
distinguishable: the question is a right-aligned violet bubble, and the answer
(with its citations disclosure and tools/cost meta) sits on a subtle surface card
— a lightly shaded, bordered block distinct from the panel background.

Sending is asynchronous and follows the Claude-app pattern: on submit the
question appears in the transcript **immediately** as an optimistic turn and the
composer clears, while the answer slot shows a **thinking indicator** until the
answer lands. The primary action becomes a live **Stop** button that cancels the
in-flight request (it is never a greyed-out, inert control); a user-initiated
stop or an API error removes the optimistic turn and restores the question to the
composer for editing/resend. **Cmd/Ctrl+Enter** sends from the textarea. The
selected conversation in the sidebar is marked with a full-perimeter ring, and
**Delete** is a two-step inline confirm (Delete → Confirm/Cancel) so a single
misclick cannot destroy a thread.

The empty state
distinguishes two cases: when **no conversations exist yet** it invites the user
to ask a first question, whereas when **conversations exist but none is selected**
it prompts the user to pick one from the sidebar or start a new one
(`[data-testid="ask-select-thread"]`).

## 1.7 Document series + comparative queries

The `compare_to_series` tool answers questions about recurring documents — a
monthly energy bill, an annual insurance renewal — by computing live statistics
over the series they belong to.

### Series detection

A **series** is the set of documents that share the same `(sender_id, kind_id)`
pair and carry an `amount_total`. The engine identifies the series automatically
from the `kind` and `sender_contains` parameters the model supplies; no user
tagging or configuration is needed. If a loose filter matches multiple
(sender, kind) combinations, the most-populous group is used.

Detection is **on the fly** — there is no materialized series table, and the
*statistics* are recomputed at query time from the live document set. The only
thing cached per series is the natural-language **description** (see below).

### Four statistical framings

Every series summary provides four views:

| Framing | What it answers |
|---------|----------------|
| **Distribution** | Mean, median, stdev, min, max over the series' amounts. |
| **Reference-vs-usual** | Where the reference document falls: `higher`, `typical`, or `lower`. |
| **Trend** | Whether amounts are `rising`, `falling`, or `flat` over time (`flat` when first→last change ≤ `SERIES_FLAT_PCT`; otherwise the sign of the least-squares slope decides). |
| **Year-over-year** | The member closest to 12 months before the reference date (within a cadence-dependent tolerance) and the percentage change. |

The cadence (`monthly`, `quarterly`, `yearly`, `irregular`) is derived from the
median gap between consecutive document dates, and influences the YoY match
tolerance.

### Typical-band rule

The `typical` verdict is given when the reference value is within **±1 stdev
OR within ±`SERIES_TYPICAL_PCT` (default 10%) of the median**. The OR ensures
that a very tight, consistent series (small stdev) doesn't flag normal variation
as `higher`/`lower`; the percent band handles the degenerate case where stdev is
zero or very small.

### Currency bucketing

Amounts in different currencies are kept separate and cannot be combined. The
bucket reported is the one matching the reference document's currency; if
unspecified, the dominant (most-document) currency is used. Other currencies
present in the series are listed in `other_currencies`.

### Cached series descriptions

Each series also carries a one- or two-sentence **LLM-generated prose
description** (e.g. *"Energy bills have crept up ~12% over the past year, peaking
in winter"*). Because that costs an LLM call, it is **precomputed and cached**
rather than generated per request:

- **Storage.** One row per `(sender_id, kind_id, currency)` in the
  `series_insights` table (`library.models.SeriesInsight`), with the description,
  the generating model, the member count it was generated over, and token/cost
  provenance. The unique key treats a NULL currency as a single bucket
  (`NULLS NOT DISTINCT`).
- **Generation.** `library.series_insight.refresh_series_insight` summarises the
  series, builds a compact stats prompt, and calls the **extraction LLM client**
  (`settings.extraction_model`, the cheap Haiku tier) to write the prose, then
  upserts the row. It is best-effort: a disabled feature, a missing API key, or
  an insufficient series all skip quietly.
- **Membership hints (W9).** If the owner has manually pinned/excluded documents
  for this series (see [api.md §1.15](api.md)), up to
  `MAX_OVERRIDE_EXAMPLES` examples per direction are appended to the prompt as a
  labelled, authoritative "curated membership" block, and the system prompt is
  told to weight them — so the description reflects the corrected series. The
  cap bounds prompt size and cost; tests assert prompt construction only (no
  live LLM call).
- **Trigger.** The `library.jobs.generate_series_insight` Procrastinate task is
  deferred whenever a document reaches `indexed` with both a sender and a kind,
  so the description refreshes as the series grows.

`summarize_series` attaches the cached description (and per-point document
`title`s for citation links) to its output; `serialise_summary` includes them in
the API body. The description is absent until the first successful generation.

### Detail-view trend widget + the /charts view

The document detail view includes a **`DocumentSeriesTrend`** panel that fetches
the document's series on mount and renders a **`SeriesChartTile`**: a Chart.js
line chart of the series' dated points (current document's point highlighted),
the cached description, a one-line verdict (e.g. *"6.4% above usual · trend
rising"*), and a list of **citation links** (each point → `/documents/{id}`). The
panel hides itself silently when `status:"insufficient"` or on fetch error.

The **`/charts`** view (sidebar nav) renders a responsive grid of the same
`SeriesChartTile`, one per eligible series, fed by `GET /api/charts`. Tiles here
have no per-document reference, so the latest member is highlighted.

The raw data is supplied by `GET /api/documents/{id}/series` and `GET /api/charts`;
see [api.md §1.13–1.14](api.md) for the wire contracts.

## 1.8 Limitations (this release)

1. **Page citations are conditional on the markdown layer.** Documents that
   have a `document_pages` row (generated by the `markdown` pipeline stage or
   `backfill-markdown`) carry a `page_number` on their citation. Documents
   ingested before the markdown layer existed, `text/plain` files, and any
   document where the markdown stage was skipped or failed will cite without a
   page number — only the document title is shown.
2. History bounding is a sliding turn window only — no rolling summarization of
   long threads.
3. RRF fusion only — no cross-encoder re-ranking.
4. Ask is in-app only; it is not exposed as an MCP tool yet.
5. CPU embedding: the one-time backfill of a large archive is slow.
