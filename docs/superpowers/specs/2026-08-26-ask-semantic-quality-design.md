# Ask & semantic pipeline: quality review

**Status:** active (2026-08-27). Plan A shipped (#96, #97, #98) and the
disclosure half of #15 with it; Plan B designed in §8 and not yet built;
Plans C and D not started.

## 1. Problem

A review of every surface that uses semantic reasoning — `/ask` (`library.ask`),
hybrid retrieval (`library.search`), structured aggregation
(`library.structured_query`), and the LLM passes in the ingestion pipeline
(`extraction/`, `matter_classifier.py`, `markdown/`, `series_insight.py`) —
found fifteen gaps. They cluster into four independent subsystems, each of which
can ship on its own.

The architecture is sound: hybrid RRF retrieval, page-aware chunking, comments
re-embedded as first-class chunks, a code-enforced preview→confirm write gate,
fail-open ingestion with per-pass budget gates. Nothing below is a redesign.
`docs/ask.md` §1.10 already records five limitations honestly; none of the
fifteen below duplicate that list.

## 2. Findings

Numbering is stable across all four plans — a task that says "closes #5" means
this section's #5.

### 2.1 Group A — answers that are confidently wrong

**#1 `sum_amount` silently drops documents.**
`structured_query.py:141` adds `Document.amount_total.isnot(None)` to the
conditions. Every document whose amount extraction failed vanishes from the
total. `QueryResult` (`structured_query.py:191`) carries only `result_type` and
`rows` — no denominator. So "how much did I spend on utilities in 2025?" returns
a number the model presents as complete when it may cover 14 of 22 bills, and
neither the model nor the user can detect it.

The quote exclusion (`structured_query.py:143-145`) is a second, *intentional*
silent drop with the same visibility problem.

**#2 Ask is blind to `review_status`.**
`extraction/validation.py` already computes which extractions are untrustworthy
— the `amount_grounding` rule fires when an extracted amount's digits do not
appear in the document text, i.e. the amount was probably hallucinated. The
document is stamped `ReviewStatus.NEEDS_REVIEW`. It then flows into `sum_amount`
weighted identically to a verified one, and `_FILTER_PROPERTIES`
(`ask/engine.py:126-152`) does not expose `review_status` even though
`DocumentFilters` (`search.py:110`) carries it. The trust signal is computed and
then discarded at the one place it decides whether a number is right.

**#3 `list_documents` truncates at 50 with no signal.**
`query_documents` hard-codes `limit: int = 50` (`structured_query.py:215`) and
the tool schema exposes no `limit`. "List every invoice from 2024" returns the
newest 50 as though that were all of them.

**#11 Phantom citations when the model cites nothing.**
`ask/engine.py:1197`: `mentioned or cited`. The fallback exists so an answer that
names its sources in prose rather than `[#id]` still gets citations — a real case,
covered by `tests/test_api_ask.py:565`. But it also fires when the answer is the
`_NO_ANSWER` sentinel, so "I couldn't find an answer to that in the archive"
ships with every retrieved-and-rejected candidate attached as a source.

### 2.2 Group B — retrieval reaches less than it could

**#5 `semantic_search` takes no filters.**
Its tool schema (`ask/engine.py:157-173`) accepts only `query`.
`query_documents` and `compare_to_series` both receive the full
`_FILTER_PROPERTIES`; the content retriever does not. "What did my 2019 mortgage
contract say about early repayment?" must search the whole archive unscoped and
hope the right chunk lands inside `retrieve_top_k = 10`.
`library.search.semantic_search` already accepts `filters: DocumentFilters`
(`search.py:436`) — the plumbing exists and is simply not wired to the tool.

**#6 Chunks embed with no document context.**
`jobs.py:337-353` embeds raw chunk text. A chunk reading `Bedrag
€ 0,00` carries no trace of the sender, the date, or the kind, so a query
naming any of them cannot match it on meaning.

**#7 Retrieval depth is fixed.**
No `top_k` on the tool; `ask_max_tool_turns = 8` bounds the number of searches.
"Find every document mentioning X" is structurally unanswerable.

**#15 There is no answer-quality eval.**
`tests/test_semantic_search.py` tests RRF mechanics; the golden corpus covers
extraction and PDF routing. Nothing measures retrieval recall or answer
correctness, so none of #5, #6 or #7 can be validated as an improvement.

### 2.3 Group C — extraction depth

**#4 Billing period vs. issue date.**
`ExtractedMetadata` (`extraction/schema.py`) has `document_date`, `due_date` and
`expiry_date` — no billing period, no line items, no consumption quantity, no
net/VAT split. For a Dutch household archive specifically:

- A bill issued 2026-01-05 covering December lands in the 2026 total.
- Monthly *termijnbedragen* and the annual *jaarafrekening* both carry an
  `amount_total`, so `sum_amount` double-counts the year.
- `compare_to_series` — the flagship "is my electricity bill higher than usual?"
  — compares euros only. It cannot separate a tariff rise from a cold month, and
  a two-month bill reads as a 100% spike.

**#8 Extraction reads the worse text layer.**
The pipeline is `ocr → extract → markdown → embed` (`jobs.py:497-518`).
Extraction runs on tesseract OCR; the Claude-vision markdown arrives afterwards.
The repair pass (`extraction/repair.py`) gets one look at that better text but is
fill-only and scoped to `sender_name`, `document_date`, `amount_total`,
`currency`. `title`, `summary`, `topics`, `kind_slug`, `recipient_name` and
`tags` are never revisited — and matter classification consumes only
`title`/`summary`/`sender` (`matter_classifier.py:151`), so a poor summary from
noisy OCR propagates into misfiling. The 800-chars-per-page vision trigger
(`extraction_vision_min_chars_per_page`) covers the worst scans; the
noisy-but-dense middle falls through.

### 2.4 Group D — UX and operability

**#9 No streaming.** `POST /api/ask` blocks for the whole loop; the frontend
shows three pulsing dots (`AskView.vue:945`). On Opus with adaptive thinking and
up to eight tool turns that is routinely 30–90 seconds of nothing. An SSE broker
already exists (`events_broker.py`).

**#10 The user cannot see what was asked.** `used_tools` is a deduplicated list
of tool *names*. Given #1 and #3 the user gets a total with no way to see it came
from `kind=utility-bill, 2025-01-01..2025-12-31` over 14 documents.

**#12 Thread memory is three turns.** `ask_history_turns: int = 3`
(`config.py:141`). Turn 5 has silently forgotten turn 1. Recorded in
`docs/ask.md` §1.10 item 2 as a known limitation; listed here because the
*user-visible* effect (the app losing the thread mid-conversation) is not
mitigated in the UI.

**#13 New matters stay empty.** Creating a matter runs no reclassification —
`api/matters.py` has no reclassify route; only the `sweep-matters` CLI does it.
A user adds "Solar panels", sees 0 documents, and concludes it is broken. Editing
a `hint` has the same problem.

**#14 Failed embeddings are invisible.** `run_embed` swallows `EmbeddingError`
into an `IngestionEvent` (`jobs.py:381`). That document is permanently absent
from semantic search with no UI surface listing it; recovery is
`library backfill-embeddings` on the host.

## 3. Goals

Per group, the shippable outcome:

- **A — trustworthiness.** No Ask answer states an aggregate without the model
  knowing, and being required to disclose, how much of the filtered set it
  actually covers and how much of it is flagged. No answer carries sources it
  did not use.
- **B — reach.** Ask can scope a content search the same way it scopes a
  structured one; chunks retrieve on sender/date/kind as well as content; every
  change is measured against a fixed question set.
- **C — depth.** Utility bills and invoices carry the billing period and, where
  printed, the consumption quantity, so period attribution and per-unit series
  comparison become correct.
- **D — feel.** The user sees the loop working, can audit what it asked, can
  populate a new matter from the UI, and can see documents missing from the
  index.

## 4. Non-goals

- Re-ranking (already deferred in `docs/roadmap.md` §1.2 item 1 with a stated
  trigger; #15's eval harness is what would *fire* that trigger).
- Per-user document scoping. The archive is deliberately one shared family
  corpus; `Document` has an `uploader_id` but no owner.
- Replacing `amount_total` with line items. #4 adds period and usage only.
- Full-corpus re-extraction for #4 — the backfill is scoped to
  `utility-bill` and `invoice`, the kinds where period attribution changes
  answers.

## 5. Plan split

| Plan | Findings | Ships |
|------|----------|-------|
| A — answer trustworthiness ✅ | #1, #2, #3, #11, #15 (disclosure) | Coverage + trust reporting on every structured result; honest citations; `library eval-disclosure` |
| B — retrieval reach | #5, #6, #7, #15 (recall) | Filters on `semantic_search`, contextual chunk headers, tunable depth, recall eval |
| C — extraction depth | #4, #8 | Period/usage fields + scoped backfill; second-look extraction on the markdown layer |
| D — UX & operability | #9, #10, #12, #13, #14 | SSE progress, tool-call transparency, matter reclassify route, index-health surface |

A was first: it had the smallest diffs, the highest correctness payoff, and it
establishes the tool-result shape (`Coverage`) that B extends.

## 6. Key design decision (Plan A): one uniform `Coverage` object

Rejected: per-aggregate ad-hoc keys (`excluded_no_amount`, `truncated`, …). They
make the `QueryResult` TypedDict a union, force the model to learn three shapes,
and each new aggregate invents a fourth.

Chosen: every `query_documents` result carries the same object.

```python
@dataclass(frozen=True, slots=True)
class Coverage:
    matched: int              # documents matching the caller's filters
    included: int             # documents contributing to `rows`
    excluded: dict[str, int]  # reason -> count; sums to matched - included
    needs_review: int         # of `included`, how many are flagged NEEDS_REVIEW
```

`excluded` is a reason→count map rather than a single reason because two drops
can apply at once (a `sum_amount` over utilities can exclude both amountless
documents and quotes). `needs_review` sits beside the completeness fields rather
than in `excluded` because a flagged document *is* counted — it is a trust
signal, not a coverage one, and the model is told to treat them differently.

The tool description makes disclosure mandatory: if `excluded` is non-empty or
`needs_review > 0`, the answer must say so.

## 7. Open question carried into Plan A

#11's deterministic fix covers the `_NO_ANSWER` sentinel. It does not cover the
model phrasing its own not-found answer ("the archive does not appear to contain
that") after a fruitless search, where the fallback still attaches every
retrieved candidate. Plan A adds a system-prompt rule requiring explicit `[#id]`
citation for any relied-upon document, which narrows but does not close this.
Closing it deterministically would mean distinguishing authoritative aggregate
results from mere retrieval candidates, which is deferred until #15's eval set
can show whether it matters.

## 8. Key design decisions (Plan B)

Added 2026-08-27, after Plan A shipped. Section 6 records Plan A's one load-
bearing decision; this records Plan B's, on the same terms. Every claim marked
**(executed)** is the output of a throwaway probe run against the test Postgres
before it was written here — see §8.7 for why that qualifier exists at all.

### 8.1 Build order: the eval is built first, #6 last

#6 is the only irreversible step in this plan — a contextual header changes what
every chunk embeds, so adopting it means re-embedding the whole corpus. #15
exists precisely so that call can be made on evidence. The order is therefore:

1. #15 layer 1 (retrieval recall) — records a baseline against today's retrieval
2. #5 + #7 — tool-schema only, no re-embed
3. #15 layer 2 (Ask-loop recall) — now has filters and `top_k` to observe
4. #6 — measured against the layer 1 baseline

#6 is not gated in the sense of "cancel it if the number does not move"; it is
gated in the sense that the number will exist and be recorded either way.

### 8.2 #5 reuses `_filters_from_args`; `review_status` stays out

`ask/engine.py` already has `_filters_from_args`, shared by `query_documents`
and `compare_to_series`, which maps the tool-schema names (`kind`, `tags`,
`projects`) onto `DocumentFilters`' storage names (`kind_slug`, `tag_slugs`,
`project_slugs`). `semantic_search` calls the same helper rather than growing a
second mapping that can drift from it.

`_REVIEW_STATUS_PROPERTY` is deliberately **not** added to `semantic_search`.
The rationale already written beside that dict holds unchanged: a filter is only
offered to a tool that can *report what the filter removed*. `semantic_search`'s
coverage block (§8.3) reports reach, not exclusion reasons, so it could honour a
`review_status` filter but not explain it.

### 8.3 `semantic_search` returns reach, and reports `unembedded` separately

The result grows a coverage block beside `results`:

```
{"matched": int, "returned": int, "unembedded": int}
```

`matched` counts documents passing the caller's filters, `returned` how many
came back (so the model can see `top_k` truncated), and `unembedded` how many of
`matched` have no chunks at all.

Both counts come from one round trip, the same conditional-aggregate shape
`count_coverage` uses in `structured_query.py`:

```python
has_chunk = exists().where(DocumentChunk.document_id == Document.id)
select(
    func.count().label("matched"),
    func.count().filter(~has_chunk).label("unembedded"),
).select_from(Document).where(*filter_conditions(filters))
```

**(executed)** against the test database: five seeded documents matching the
filter, two of them chunkless, returns `matched=5 unembedded=2`.

`unembedded` is not in the original #5 and is worth justifying. A probe seeding
two filter-matching documents, only one of which had chunks, returned
**(executed)** `matched=2, hits=1`. So `matched` on its own conflates three
situations the model must respond to differently:

- the filter was too narrow (`matched` small) — widen it and retry
- the archive is genuinely silent (`matched` large, `unembedded` zero, no hits)
  — say so
- the documents exist but are not indexed (`unembedded` non-zero) — the answer
  is incomplete for an operational reason, not an archival one

That third case is finding #14 (failed embeddings are invisible) surfacing
inside #5. Reporting it here does not fix #14 — the operator still has no UI for
it — but it stops Ask from confidently reporting an indexing gap as an absence.

### 8.4 #7 exposes `top_k` only, and the clamp is load-bearing

`top_k` is offered; `chunks_per_doc` is not. One knob is one thing for the model
to get right, and "find every document mentioning X" is a breadth problem.
A new `ask_search_max_top_k: int = 50` setting caps it, so the ceiling is
configurable and documented rather than a literal.

The clamp is **not** defensive tidiness. `semantic_search` ends in
`ranked[:top_k]`, so a negative `top_k` slices from the end and silently returns
a near-arbitrary subset. Measured **(executed)** against seven matching
documents:

| `top_k` | hits returned |
|---------|---------------|
| `-3`    | **4**         |
| `-1`    | **6**         |
| `0`     | 0             |
| `1`     | 1             |
| `1000`  | 7             |

A model that emits `top_k: -1` today gets six results and no error. The
implementation clamps to `max(1, min(int(top_k), ask_search_max_top_k))` and the
test asserts every row of that table.

`chunks_per_doc` needs no such guard — `semantic_search` gates the multi-passage
path on `if chunks_per_doc > 1`, so `-1` and `0` both degrade to one passage
**(executed)**. This is recorded so a later change to that guard is understood
to be removing a protection, not tightening one.

### 8.5 #6 stores the header in its own column, and re-embeds on metadata edit

**Storage.** A new nullable `document_chunks.context_header` column (migration
0031). `run_embed` composes `sender · date · kind · title` (omitting fields the
document lacks), embeds `header + "\n\n" + text`, and stores the header there
while `text` keeps the raw passage.

The alternative — prepending the header to `text` — was rejected because `text`
is not only what gets embedded, it is also what Ask *reads*: **(executed)**
confirms `SemanticHit.chunk_text` and `chunk_texts` return the stored `text`
verbatim. With `retrieve_chunks_per_doc = 3` and `top_k = 10`, a baked-in header
would repeat the same sender/date/kind up to thirty times per tool result,
duplicating fields the result rows already carry as structured values. The
separate column also makes the embedded text auditable: a bad retrieval can be
diagnosed against the header that was actually embedded rather than one
re-derived from current metadata and assumed to match.

Comment chunks receive the same document header. Their own `User comment
(date):` framing stays in `text` — the two are complementary, not competing.

**Staleness.** A header embedded at ingest goes stale when metadata changes
later. Ingest itself is safe: `EMBED` is the last pipeline stage, after
`MARKDOWN` and its repair pass, so a freshly ingested document's header sees
final metadata. The exposure is later edits.

`apply_document_update` returns the list of changed storage-level field names
and has exactly two call sites (`api/documents.py` and `ask/engine.py`), both
already followed by `revalidate_after_edit`. A re-embed is enqueued from beside
that call, and **only** when the changed set intersects the header fields
(`sender_id`, `document_date`, `kind_id`, `title`). A `summary`, `tags` or
`projects` edit therefore never touches the embedder. `api/comments.py` is the
existing precedent for "an edit defers `embed_document`".

The honest cost: Ask's own confirmation-gated write tool can now enqueue an
embed mid-conversation. That is the intended behaviour — an agent that corrects
a sender should not leave the index describing the old one — but it is new
coupling between the write path and a network sidecar, and the sidecar being
down must remain non-fatal, exactly as `run_embed` already treats it.

Rollout uses the existing `library backfill-embeddings --include-existing`. No
new command. Chunks written before the migration carry a NULL header until that
runs; §1.10 of `docs/ask.md` records this.

### 8.6 #15 is two layers over a synthetic corpus

The two layers measure different things and have different dependencies, which
is why they are two:

- **Layer 1 — retrieval recall.** Calls `library.search.semantic_search`
  directly and scores recall@k. Needs the bge-m3 embedder; needs **no Claude
  credentials**. Deterministic, so it can attribute a change to #6.
- **Layer 2 — Ask-loop recall.** Drives `run_ask` and scores
  `AskResult.citations` against the expected document ids. Needs Claude
  credentials. This is the only layer that can show whether the model
  *actually uses* the filters and `top_k` that #5 and #7 add — a schema the
  model ignores is indistinguishable from no schema at layer 1.

Scoring against `citations` rather than the answer prose is deliberate:
`AskCitation` already carries `document_id`, so layer 2 needs none of the
heuristic text-screening `disclosure_eval.mentions_count` required, and it
incidentally exercises Plan A's #11 fix.

**The corpus is synthetic**, authored in this repository. The real golden corpus
was considered and rejected on two grounds: fifteen documents is too thin a
haystack for recall@10 to discriminate, and the question→expected-id mapping
would have to live in the private repo, splitting the eval across two
repositories. Synthetic documents keep the whole eval readable in the public
repo with no secret, matching how `disclosure_scenarios.py` already works, and
carry a `(recall-eval fixture)` sender suffix for the same reasons.

Synthetic text brings one serious risk, and it is the risk that decides whether
this eval is worth building: **text that is too easy makes recall@10 trivially
1.0 and measures nothing.** Mitigations are part of the deliverable, not
afterthoughts — each case ships with near-miss distractors (same kind, same
sender, adjacent dates, overlapping vocabulary) over a haystack of roughly
40–60 documents. And the eval carries an acceptance criterion on *itself*: if
baseline recall@10 comes out at or above 0.9, the scenarios are too easy and are
made harder before anything downstream is built. An eval with no headroom to
fall cannot show #6 helping.

**Where it runs.** `library eval-recall`, reusing the outer-transaction binding
`eval-disclosure` already established so nothing is committed, plus a step in
`e2e-nightly.yml` after the embedder-warm wait. That workflow's own header
already argues this class of check — embedder-dependent, not fully
deterministic — belongs nightly and must not gate merges. It is also the only
place with a warm embedder that runs on its own: TEI publishes no arm64 image,
so an Apple Silicon laptop cannot host one, and a command nobody remembers to
run is the `E2E_SMART_GROUPS` failure repeated. A committed baseline value makes
#6's effect a diff rather than a recollection.

`recall_eval.py` is pure and stdlib-only, like `disclosure_eval.py`, so its
scoring is unit-tested in CI with no embedder and no credentials. It inherits
`disclosure_eval.score`'s hard-won guard: a case with an empty expected set must
**fail**, not vacuously pass having exercised nothing.

### 8.7 On the **(executed)** qualifier

Across the two Plan A branches, twelve defects originated in the plan documents
and none in the implementations; the parts prototyped against the real test
database before being written down landed clean both times. Every factual claim
above about how shipped code behaves was therefore produced by running a
throwaway probe, not by reading. The probes were deleted once their output was
recorded here.

Three of the decisions above exist *only* because a probe contradicted what
reading suggested: `unembedded` (§8.3), the negative-`top_k` clamp (§8.4), and
the confirmation that a separate header column needs no change to `search.py`
(§8.5). Implementers and reviewers of Plan B should treat this section as
fallible in the same way and verify against shipped code.
