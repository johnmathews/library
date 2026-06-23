# Document series + comparative queries — design

**Status:** approved (2026-06-22). Sub-project 5 of 5 in the extraction/Ask roadmap (the final one).

## 1. Goal

Give the archive two related capabilities:

1. **Series detection** — recognise recurring documents (e.g. the monthly energy
   bill from the same provider) as a *series*.
2. **Comparative queries** — answer questions like *"is this electricity bill
   more expensive than usual?"*, *"how does this month compare to last year?"*,
   and *"are my bills going up?"* against that series, with citations.

Builds directly on the structured-metadata layer from sub-projects 1–2
(`sender`, `kind`, `document_date`, `amount_total`, `currency`) and the agentic
Ask loop from sub-project 4.

## 2. Decisions (settled in brainstorming)

| Question | Decision |
|----------|----------|
| How is a series identified? | **Auto-grouping by `(sender_id, kind_id)`**, with a cadence derived from date gaps. No explicit user grouping, no clustering. |
| Materialized or on-the-fly? | **Computed on the fly.** No new table, no migration, no pipeline stage. |
| How does comparison reach Ask? | **A new third tool `compare_to_series`**, alongside `semantic_search` and `query_documents`. |
| Which statistics / framings? | **All four:** distribution stats, reference-vs-usual, year-over-year, trend direction. |
| UI scope | **Ask + a trend widget on document detail.** No standalone series browser this release. |
| "Typical" band | `reference` is "typical" when within **±1 stdev OR within ±`SERIES_TYPICAL_PCT` of the median** (the OR keeps a very tight series from flagging normal noise as higher/lower). |

## 3. Architecture

A new module **`src/library/series.py`**, parallel to `structured_query.py`,
owns all series logic. Two consumers call one entry point so they cannot drift —
the same pattern as `search.py` serving both REST and MCP.

```
                        ┌─ ask.engine: compare_to_series tool ─┐
library/series.py ──────┤                                      ├──▶ summarize_series()
  summarize_series()    └─ api/documents: GET /{id}/series ────┘
        │
        ├─ reuses DocumentFilters + filter_conditions (library.search)
        └─ reads sender_id, kind_id, document_date, amount_total, currency
```

- **Pure read-side.** No new table, no migration, no pipeline stage.
- Returns frozen dataclasses (like `structured_query.AmountGroup`); consumers
  serialize them. Money serialized as `str(Decimal)`, dates as ISO strings —
  matching `structured_query.py`.
- Every result carries contributing `document_ids` (capped at the reused
  `MAX_CITED_IDS = 25`) for citation.
- Frontend: a new self-contained **`components/DocumentSeriesTrend.vue`** that
  renders the trend chart with **Chart.js (via `vue-chartjs`)** — a new,
  deliberately chosen dependency, since this widget is expected to grow (more
  chart types, a future series UI). Mounted in `DocumentDetailView.vue`, which
  is already ~49k and should not grow. Chart.js components are registered
  locally/tree-shaken (not globally) and the widget is lazy-loaded.

## 4. Series detection & statistics

`summarize_series(session, *, filters, reference)` performs:

1. **Identify the series.** Select non-deleted documents matching `filters`;
   the series is the `(sender_id, kind_id)` group they fall in (both must be
   non-null). In practice `filters` carries `kind_slug` + `sender_contains`
   (Ask) or is derived from a document's own sender/kind (endpoint).
2. **Threshold.** If fewer than `SERIES_MIN_DOCUMENTS` (default **3**) usable
   members, return `status="insufficient"` with the count — never fabricated
   stats.
3. **Currency bucketing.** Amount stats are computed per currency (amounts in
   different currencies cannot be combined, as in `sum_amount`). The reference's
   currency selects the reported bucket; absent a reference, the **dominant**
   (most-documents) currency is used. Other currencies present are noted.
4. **Distribution stats** over `amount_total` within the bucket: `count`,
   `mean`, `median`, `stdev` (sample stdev; `0` when n<2), `min`, `max`.
5. **Cadence.** Median gap (days) between consecutive `document_date`s →
   `monthly | quarterly | yearly | irregular` (nearest band within tolerance;
   else `irregular`). The series is defined by its **amount-bearing** members
   (the comparison is amount-centric); documents with no amount, or no date,
   take no part in the stats or cadence.
6. **Reference-vs-usual.** `reference` is `"latest"` (the newest dated member),
   an explicit numeric amount, or a document id. Report `value`, absolute Δ,
   `vs_median_pct`, `z_score` (Δ/stdev, `null` when stdev=0), and a `verdict`
   of `higher | typical | lower` per the §2 band.
7. **Trend.** Least-squares slope sign over (date-ordinal, amount) → `rising |
   falling | flat`, where `flat` is `|change_pct| ≤ SERIES_FLAT_PCT`; plus
   `change_pct` first→last.
8. **Year-over-year.** Find the member(s) ≈12 months before the reference date
   (tolerance derived from cadence, e.g. ±45 days for monthly). Report the
   matched document's value, `change_pct`, and `document_id`, or `null` when no
   match exists.

### Result shape (JSON-friendly)

```jsonc
{
  "status": "ok",                      // or "insufficient"
  "sender": "Vattenfall",
  "kind": "utility-bill",
  "currency": "EUR",
  "other_currencies": [],              // currencies present but not reported
  "cadence": "monthly",
  "count": 7,
  "mean": "145.00", "median": "142.10", "stdev": "8.20",
  "min": "131.00", "max": "159.40",
  "reference": {
    "value": "151.20",
    "delta": "+9.10",
    "vs_median_pct": "+6.4%",
    "z_score": "1.11",
    "verdict": "higher"
  },
  "trend": { "direction": "rising", "change_pct": "+12.0%" },
  "year_over_year": { "prior_value": "138.40", "change_pct": "+9.2%", "document_id": 41 },
  "document_ids": [12, 19, 27, 33, 41, 55, 88]
}
```

The `insufficient` shape carries `status`, `count`, and the resolved
`sender`/`kind` so callers can explain why.

## 5. Ask integration — `compare_to_series` tool

A third entry in `TOOLS` (`ask/engine.py`), dispatched alongside the existing
two.

```jsonc
compare_to_series({
  "kind": "utility-bill",            // concept→kind hints reused via _kind_hint()
  "sender_contains": "vattenfall",
  "date_from": "...", "date_to": "...",   // optional window
  "reference": "latest"              // "latest" | a number; default "latest"
})
```

- Tool **description**: use for "more/less than usual", "compared to last year",
  "are my bills going up" questions; identify the series via `sender`/`kind`.
  Includes the `_kind_hint()` concept→kind map.
- Dispatch (`_run_compare_to_series`) builds `DocumentFilters` from the args
  (reusing `_parse_date`), calls `summarize_series`, and adds the returned
  `document_ids` to the `cited` set — citations flow through unchanged.
- The system prompt gains one line describing the third tool.
- Loop bound, cost accounting, history replay, and prompt caching are
  **unchanged** — this is just another tool in the existing loop.

## 6. Document-detail endpoint + trend widget

- **`GET /api/documents/{id}/series`** (in `api/documents.py`): loads the
  document (user/visibility rules as for the existing detail/markdown routes),
  derives `filters` from its own sender + kind, calls `summarize_series` with
  `reference=<this document id>`, and returns the §4 shape **plus** a `points`
  array (`[{ "date": "ISO", "amount": "142.10" }, ...]`, oldest→newest, bucket
  currency) for the sparkline.
  - `404` if the document is missing/foreign.
  - `200` with `status:"insufficient"` if the document has no sender/kind or the
    series is too small — the UI hides the widget rather than erroring.
- **`components/DocumentSeriesTrend.vue`**: a lazy-loaded panel on the detail
  view. Renders a **Chart.js line chart** (via `vue-chartjs`) of `points` with
  the current document's point highlighted, a one-line verdict (e.g. *"≈6% above
  usual · trend rising"*), and the cadence label. Hidden gracefully on
  `insufficient`/`404`. Uses the same fetch client and error handling as the
  existing markdown tab. Chart.js controllers/elements are imported and
  registered within the component (tree-shaken), not globally.

## 7. Configuration

New settings (`config.py`, `.env.example`, `docs/ask.md`), `LIBRARY_` prefix:

| Setting | Default | Purpose |
|---------|---------|---------|
| `LIBRARY_SERIES_MIN_DOCUMENTS` | `3` | Minimum members before stats are reported. |
| `LIBRARY_SERIES_TYPICAL_PCT` | `0.10` | Half-width of the "typical" band as a fraction of the median (used with the ±1 stdev OR). |
| `LIBRARY_SERIES_FLAT_PCT` | `0.05` | Absolute first→last change at or below which the trend is `flat`. |

## 8. Error handling

- **Insufficient data** → structured `status="insufficient"`, never a crash or
  fabricated stats.
- **Mixed currencies** → report the reference/dominant bucket; list the others
  in `other_currencies`.
- **Missing amounts** → excluded from the series entirely (it is amount-defined);
  they do not contribute to stats or cadence.
- **No YoY match** → `year_over_year: null`.
- **stdev = 0** (identical amounts / n<2) → `z_score: null`; verdict falls back
  to the percent band.

## 9. Testing

- **`series.py` unit tests** (seeded documents): distribution math, cadence
  classification (monthly/quarterly/yearly/irregular), verdict bands
  (higher/typical/lower incl. the stdev-OR-pct edge), trend direction incl.
  flat band, YoY matching incl. no-match, insufficient/empty, multi-currency
  bucketing.
- **Ask-engine test**: `compare_to_series` dispatches, returns the shape, and
  its `document_ids` reach the citation set.
- **API tests** for `GET /api/documents/{id}/series`: ok, insufficient,
  no-sender/kind, 404, foreign document.
- **Frontend `DocumentSeriesTrend.spec.ts`**: renders the chart (Chart.js
  mocked) + verdict; hides on insufficient/404.
- **Docs**: extend `docs/ask.md` (new tool + capability, config table) and
  `docs/api.md` (new endpoint); add a journal entry
  `journal/260622-document-series.md`.

## 10. Out of scope (this release)

- Materialized series table / pipeline stage (chose on-the-fly).
- Standalone series-browser page (chose detail-view widget only).
- Cross-encoder re-ranking, MCP exposure of `compare_to_series`.
- Forecasting / anomaly alerts beyond the simple verdict + trend.
