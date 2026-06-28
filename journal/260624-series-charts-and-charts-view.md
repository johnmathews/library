# Per-document series charts + `/charts` aggregate view

Implemented **item 5** of the deferred webapp improvement plan
(`.engineering-team/runs/manual-20260624T080332Z/improvement-plan.md` §2.3) — the
last sub-project in the Extraction/Ask roadmap. Built end-to-end through the
engineering-team cycle (worktree → doc → tests → code → suite + lint → review →
journal → merge), as five work units.

## What shipped

1. **Cached, LLM-generated series descriptions.** A series — one
   `(sender_id, kind_id, currency)`, e.g. EUR utility bills from one provider —
   now carries a one/two-sentence prose summary ("Energy bills have crept up ~12%
   over the past year, peaking in winter"). The *stats* are still computed on the
   fly; only the prose is cached.
2. **Per-point citation links.** `points[]` already carried `document_id`; each
   point now also carries the document `title`, so the chart can link every point
   to `/documents/{id}`.
3. **Standard tile chrome + description.** The detail-view chart is now a shared
   `SeriesChartTile` with the app's card chrome, the description text, the verdict
   line, and the citation list.
4. **`/charts` aggregate view.** New `ChartsView` (sidebar nav entry) renders a
   responsive grid of `SeriesChartTile`, one per eligible series, fed by a new
   `GET /api/charts`.

## Backend

- **`SeriesInsight` model + migration `0009`** (`series_insights` table). One row
  per `(sender_id, kind_id, currency)`, with the description, generating model,
  `member_count` (how many docs it was generated over — lets a stale description
  be spotted), and token/cost provenance. Unique key uses
  **`NULLS NOT DISTINCT`** (Postgres 15+) so a NULL-currency series is a single
  bucket rather than allowing duplicate rows.
- **`library.series_insight`** — `build_series_prompt` (pure), `generate_description`
  (one `messages.create` call), and `refresh_series_insight` (summarise → generate
  → upsert). Reuses the extraction LLM client and `settings.extraction_model` (cheap
  Haiku). Best-effort, mirroring `extraction.apply`: disabled feature / missing key /
  insufficient series all skip quietly. `client` is injectable for tests; built from
  settings (and closed) otherwise.
- **Job + trigger.** `library.jobs.generate_series_insight(sender_id, kind_id)`,
  deferred from `advance_pipeline` when a document reaches `indexed` with both a
  sender and a kind — best-effort like the thumbnail defer (the document is already
  committed `INDEXED`, so a queue hiccup can't strand it).
- **`series.py` integration.** `_Member`/`SeriesSummary` gained `sender_id`,
  `kind_id`, per-point `titles`, and `description`; `summarize_series` attaches the
  cached description via `load_series_description`; `serialise_summary` emits
  `sender_id`, `kind_id`, `description` (when present), and per-point `title`.
  Additive — Ask's `compare_to_series` and the MCP path are unaffected.
- **`GET /api/charts`** (`library.api.charts`, registered in `app.py`). Enumerates
  eligible `(sender, kind)` groups (`GROUP BY … HAVING count >= series_min_documents`)
  then summarises each, omitting any that come back `insufficient` (the dominant
  currency bucket can still be too small after the count passes). Behind the same
  `current_user` + CSRF router guard as everything else.

## Frontend

- **`SeriesChartTile.vue`** — presentational: takes a `DocumentSeries` + optional
  `highlightDocumentId`, renders the Chart.js line chart (highlighted point), the
  cached description, the verdict/trend line, and a citation list (each point →
  `RouterLink` to its document).
- **`DocumentSeriesTrend.vue`** — slimmed to a thin fetch wrapper that delegates to
  `SeriesChartTile` (passing `highlightDocumentId = documentId`); self-hides on
  `insufficient`/error as before.
- **`ChartsView.vue`** + `/charts` route + sidebar **Charts** nav link
  (`sidebar-charts-link`) + `fetchCharts()` in `documents.ts`. Loading / empty /
  error states.

## Key decisions

- **Precompute & cache the description in the DB** (decided with the user), not
  on-demand and not template-only — the chart and `/charts` never block on an LLM
  call.
- **Refactor over duplicate.** Rather than a second chart component for `/charts`,
  extracted the shared `SeriesChartTile`; the detail-view wrapper and the grid both
  feed it.
- **Tests at every layer** (the user's standing requirement): pytest for the model/
  migration, generation, serialisation, the job defer, and the endpoint; vitest for
  the tile, citations, the wrapper delegation, the charts view, and the nav link.

## Code-review fixes (caught in wrap-up, not in the dev loop)

- **Upsert race → `INSERT … ON CONFLICT DO UPDATE`.** The first cut of
  `_upsert` did SELECT-then-INSERT. Two documents in the same series indexed close
  together queue two refresh jobs that could both read "no row" and both INSERT —
  the loser hitting the unique constraint and failing the job. Rewrote it as a
  single atomic Postgres upsert keyed on the constraint; added a test that
  pre-seeds a (stale) row and asserts refresh updates it without duplicating.
- **`vs_median_pct` sign strip.** Replaced the carried-over
  `.replace('+','').replace('-','')` with `.slice(1)` (the value is always a signed
  `"+30.0%"`/`"-5.2%"` string) — clearer intent, same result.
- **Docs.** Dropped a misleading `≈` glyph from two illustrative verdict examples;
  completed the `frontend.md` §1.3 sidebar nav list (it was already stale, missing
  Jobs, and now Charts).

## Notes / follow-ups

- **Coverage measurement.** `api/charts.py` reports ~67% and `api/documents.py`
  ~54%, but the uncovered lines are the async route bodies — FastAPI's TestClient
  runs them in a portal thread `coverage` doesn't track by default. The routes are
  exercised by passing integration tests; the real logic modules are well covered
  (`series.py` 94%, `series_insight.py` 87%, `ChartsView.vue` 100%, `documents.ts`
  94%). Backend total 90%, frontend 90%.
- **`/api/charts` does `1 + 2K` queries** (1 eligibility + 2 per series: members +
  cached description). Acceptable for a single-household archive; if series counts
  grow, batch the per-series summarise or cache the whole payload.

## Results

- Backend: **518 passed**, ruff clean. Frontend: **328 passed**, eslint + vue-tsc
  clean, production build OK, `check:assets` OK.
