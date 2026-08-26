# Ask & semantic pipeline: quality review

**Status:** design, awaiting review (2026-08-26)

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
`jobs.py:337-353` embeds raw chunk text. A chunk reading `Termijnbedrag
€ 142,50` carries no trace of the sender, the date, or the kind, so a query
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
| A — answer trustworthiness | #1, #2, #3, #11 | Coverage + trust reporting on every structured result; honest citations |
| B — retrieval reach | #5, #6, #7, #15 | Filters on `semantic_search`, contextual chunk headers, tunable depth, recall eval |
| C — extraction depth | #4, #8 | Period/usage fields + scoped backfill; second-look extraction on the markdown layer |
| D — UX & operability | #9, #10, #12, #13, #14 | SSE progress, tool-call transparency, matter reclassify route, index-health surface |

A is first: it has the smallest diffs, the highest correctness payoff, and it
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
