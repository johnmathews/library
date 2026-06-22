# Document series + comparative queries

**Date:** 2026-06-22
**Branch:** `feat/document-series-comparative`
**Sub-project:** 5 of 5 — this completes the extraction/Ask roadmap.

---

## What shipped

A new capability answers comparative questions about recurring documents: *"is
this electricity bill higher than usual?", "how does it compare to last year?",
"are my bills going up?"*. It consists of:

- `src/library/series.py` — series detection and statistical engine
- A third Ask tool `compare_to_series` in `src/library/ask/engine.py`
- A REST endpoint `GET /api/documents/{id}/series` in `src/library/api/documents.py`
- A frontend trend widget `components/DocumentSeriesTrend.vue` (Chart.js)

---

## Brainstorming decisions

Five design questions were settled before implementation:

1. **Auto-grouping by `(sender_id, kind_id)`.** No explicit user grouping, no
   clustering, no tagging. The series emerges naturally from the metadata already
   extracted. If a filter matches multiple (sender, kind) pairs the most-populous
   group wins — keeps the API simple and avoids fabricating a mixed series.

2. **On-the-fly computation.** No new table, no migration, no pipeline stage.
   The stats are recomputed from the live document set at query time. A
   materialized table would add pipeline complexity for a feature that is
   read-only and read-rarely (per-document widget, not a background scan).

3. **A new third Ask tool `compare_to_series`.** Adding it as a peer to
   `semantic_search` and `query_documents` keeps the architecture uniform: Claude
   selects the right tool, the tool returns structured data, citations flow
   through the existing path unchanged. No new endpoint for Ask, no prompt
   special-casing.

4. **All four framings.** Distribution stats alone answer "how much do I usually
   pay?"; reference-vs-usual answers "is this bill normal?"; trend answers "are
   costs going up?"; year-over-year answers "vs last year?". Providing all four
   means Claude can answer a wide range of comparative questions from one tool
   call.

5. **Ask + detail widget only.** No standalone series-browser page in this
   release. The widget on the document detail view is the natural place to surface
   the series for an individual document; it is self-contained and can be extended
   later without touching the existing document list or Ask views.

---

## `series.py` design

The module follows the same pattern as `structured_query.py` and `search.py`:
pure-function helpers at the top, one async orchestrator (`summarize_series`) at
the bottom, and a serialiser (`serialise_summary`) that converts the frozen
dataclasses to a JSON-friendly dict. Two consumers (`ask/engine.py` and
`api/documents.py`) call the same entry point so they cannot drift.

Helper breakdown:
- `distribution(amounts)` — count/mean/median/stdev/min/max; sample stdev with
  n<2 falling back to 0.
- `classify_cadence(dates)` — median gap → monthly/quarterly/yearly/irregular
  via fixed bands.
- `compare_reference(value, dist, typical_pct)` — verdict with the OR rule (see
  below).
- `compute_trend(points, flat_pct)` — least-squares slope; flat band applied
  first.
- `year_over_year(points, reference_date, cadence)` — nearest member within a
  cadence-derived tolerance window.

`summarize_series` loads members via `_load_members`, picks the dominant
(sender, kind) group, buckets by currency, computes all statistics, and returns a
frozen `SeriesSummary` dataclass.

---

## Typical-band rule

The `typical` verdict uses **±1 stdev OR ±`SERIES_TYPICAL_PCT` of the median**
(default 10%). The OR is deliberate:

- A very tight, consistent series (small stdev) would otherwise flag any normal
  noise as `higher`/`lower`.
- The percent band ensures the verdict is intuitive even when stdev is zero
  (identical amounts) or very small.

Both conditions are checked; if either is satisfied the verdict is `typical`.

---

## Distribution includes the reference

An early alternative considered treating the reference as an out-of-sample
point (exclude it from distribution stats). The simpler approach — include all
series members including the reference in the distribution — was chosen because:

- The distribution represents the whole history; the reference is part of that.
- Excluding it would make the stats change depending on which document you view,
  which is surprising.
- The endpoint is read-only and cheap to recompute.

---

## Dominant-currency bucketing

Amount stats cannot be combined across currencies. When the reference document
has a currency, its bucket is used. Otherwise the bucket with the most documents
wins. Other currencies are listed in `other_currencies` so the caller knows the
series is mixed.

This matches the design principle from `structured_query.py` (`sum_amount` also
groups by currency) — consistent behaviour across both query paths.

---

## Chart.js via `vue-chartjs`

`vue-chartjs` was added as a new dependency (deliberately chosen). Reasons:

- The trend widget is expected to grow: other chart types (bar, scatter),
  possibly a future series-browser page.
- Chart.js is a well-maintained, tree-shakeable library; components and
  controllers are registered locally within `DocumentSeriesTrend.vue`, not
  globally, so unused chart types are not bundled.
- The widget is a self-contained component mounted from `DocumentDetailView.vue`
  (which was already large); it fetches its series data on mount and self-hides
  when there is no qualifying series, so the detail view stays focused. (Code
  splitting via `defineAsyncComponent` was considered but skipped — marginal
  benefit for a self-hosted app, and it complicates the detail-view test.)

---

## `document_id` in points — highlight fix

The initial design returned points as `{date, amount}` tuples. The frontend
needs to identify which point corresponds to the viewed document in order to
highlight it on the chart. Adding `document_id` to each point resolved this
cleanly without a second API call. `serialise_summary` accepts
`include_points=True` (set only by the REST endpoint, not the Ask tool) to keep
the Ask response compact.

---

## Shared-DB test isolation

The new `series.py` tests and API tests for `GET /api/documents/{id}/series`
exposed a pre-existing test-isolation gap: a handful of tests were relying on
implicit shared database state between test functions. The series tests, which
require precise control over which documents exist, made these collisions visible.
Each affected test was updated to use function-scoped fixtures or explicit
teardown. All 447 backend tests pass.

---

## Roadmap complete

Sub-project 5 is the last item in the extraction/Ask roadmap:

| # | Sub-project | Status |
|---|-------------|--------|
| 1 | Extraction quality (deterministic validation) | Done |
| 2 | Markdown layer + page citations | Done |
| 3 | Page citations (folded into #2) | Done |
| 4 | Conversational Ask (threads, history, caching) | Done |
| 5 | Document series + comparative queries | **Done** |

The archive can now answer content questions, aggregation questions, and
comparative questions — with citations — in a persistent multi-turn conversation,
with per-document trend context in the detail view.
