# 1. Charts: delete, filtering, grouping, full-screen view, export/share

Date: 2026-07-01

## 1.1 What shipped

A feature pass over the `/charts` grid and `/charts/:seriesId` single-chart page, driven end-to-end through the engineering-team cycle (targeted recon → plan → build → verify). All frontend — no backend, model, or migration changes.

1. **1.1.1 Delete a chart.** Authored series now have a **Delete** affordance (`series-delete`) with a two-step inline confirm (no blocking dialog) that calls the already-existing `DELETE /api/charts/authored/{id}`. On the grid the tile is dropped locally; on the detail page it routes back to `/charts`. Emergent (auto-detected) series have no delete — they are computed from documents, not stored.
2. **1.1.2 Better filtering.** Added **Last quarter** and a **Custom range** with two `AppDateInput` datepickers to `useChartsTimeframe`. Picking a preset reflects its resolved window into the datepickers; editing a datepicker flips the selection to Custom. All persisted per-browser.
3. **1.1.3 Grouping.** New `useChartsGrouping` + `groupSeriesPoints`: buckets a series' per-document points into week/month/quarter/year periods and **sums** the amounts into one bar per period, client-side. Ungrouped mode is unchanged (one bar per document, active bar highlighted).
4. **1.1.4 Full-screen detail view.** Removed the `max-w-2xl` box; the tile renders at full width and taller (`size="large"`). A toolbar hosts the shared `ChartControls` plus export/share.
5. **1.1.5 Export & share.** `chartExport.ts`: Download **PDF** (`jspdf`), **JPEG**/**PNG** (Chart.js canvas → white-composited data URL), and **Copy link** (`navigator.clipboard`). The tile exposes `getChartCanvas()`.
6. **1.1.6 Click-to-open tiles.** The tile heading and the whole chart area now navigate to the single-chart page; the previously-inert card is live. Edit / document controls still work.

## 1.2 Key findings from reconnaissance

- **1.2.1** The **delete endpoint and its frontend API wrapper (`deleteAuthoredSeries`) already existed** — only the UI was missing. Delete was a wiring job, not new backend work.
- **1.2.2** **Multi-user visibility was already the behavior.** Charts and documents are deliberately unscoped (`api/charts.py:99-107`: "Library is a single shared family archive … owner_id is provenance, not an access boundary"). The user's ask ("other users should see it even though john is the owner") was already satisfied, so no ACL work was done — confirmed with the user before planning.
- **1.2.3** Filtering was frontend-only Chart.js axis clamping; **grouping did not exist anywhere** (points are emitted per-document). Both stayed client-side per the user's "view-time, per browser" choice — keeping the whole change migration-free.

## 1.3 Shared control surface

`ChartControls.vue` is presentational: the parent view owns the `useChartsTimeframe` / `useChartsGrouping` state (needed to compute axis bounds + grouping for tiles) and passes values + handlers down. `useStorage` does not sync sibling instances within one tab, so a single owner per view is deliberate — both views share the same storage keys, so a chart opens with the last-used range/grouping.

## 1.4 Verification

- Frontend: full unit suite green (572 tests), `type-check` + `lint` clean. New/extended specs cover the timeframe presets + custom reflection, grouping sums, click-to-open navigation, delete (tile + both views), `ChartControls`, and `chartExport`.
- Backend: full suite green (86% coverage), `make lint` clean — no backend code changed.
- E2E: added `charts.spec.ts` (create → click-to-open → export buttons present → delete), self-skipping locally and validated by CI against the real stack across the device matrix.
