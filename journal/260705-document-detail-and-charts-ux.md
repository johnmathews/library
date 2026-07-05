# Document-detail & charts UX: editable hero, chart controls, clickable sources

Date: 2026-07-05

Five user-requested UX changes across `/documents/:id` and `/charts`, plus two
answered questions. All work is frontend (Vue 3 + TS + Chart.js); no backend or
DB change — every field needed was already served by the API. Shipped via the
engineering-team workflow (evaluate → plan → develop → wrap-up) on branch
`worktree-eng-doc-charts-ux`.

## 1. What shipped

1. **History newest-first** — `DocumentHistoryTimeline.vue` sorted the events
   timeline oldest-first; flipped the single comparator to newest-first (stable
   sort keeps equal timestamps in incoming order). Feeds both the milestone view
   and the "Show all events" log.
2. **Clickable chart source (both modes)** — in the shared `SeriesChartTile.vue`:
   ungrouped bars are click-through to `/documents/{id}` (pointer cursor on
   hover; `stopPropagation` so a bar click opens the *document*, not the chart
   page). Grouped bars — where one bar aggregates several documents — get a
   custom **external HTML sticky tooltip** that stays open while hovered and
   lists each contributing document as a clickable link. `document_id` is now
   threaded through `useChartsGrouping` bucketing (it was previously dropped).
   Benefits the charts grid, the single-chart page, and the document-detail
   trend at once.
3. **Doc-detail chart controls** — `DocumentSeriesTrend.vue` gained the same
   `ChartControls` bar (time range / from / to / group by) as `/charts`, with
   matching defaults (**Last 12 months**, group by **month**) but **separate**
   localStorage keys so the two surfaces don't move together. Timeframe
   filtering is client-side; empty window renders a graceful empty-state.
4. **Unified "Edit layout" mode** — new `useDocumentLayout` composable
   (localStorage, per-user, all documents) owns hero-field visibility+order and
   section-card order plus an ephemeral edit-mode flag. A single page toggle in
   the hero enters customization for both. Uses `sortablejs` (already a dep).
5. **Hero: recipient + customization** — the hero now shows **recipient** (was
   missing) and renders from the layout: read mode shows fields that are both
   visible and populated, in saved order; edit mode lists every known field with
   a visibility toggle and drag handle. Value editing stays in the Details card.
6. **Section cards reorder** — the two-column grid renders each column from the
   persisted `cardOrder`, reorderable within a column via drag handles.
   Cross-column moves are out of scope for v1 (the responsive `lg:grid-cols-2` /
   `lg:order-*` split makes them fiddly).

## 2. Two questions answered during reconnaissance

1. **Does editing a document re-run extraction?** No — and for recipient it
   needn't. The extractor prompt is built only from OCR text / the raw file
   (`extraction/extractor.py`); it never feeds existing metadata back in, so
   `recipient` is an LLM *output*, not an *input* — correcting it invalidates
   nothing downstream. Edits are recorded in `user_edited_fields` so future
   re-extraction won't overwrite them. Only a *note body* edit re-processes
   (the body is its own source text).
2. **Do we use embeddings?** Yes — local bge-m3 (1024-dim) → pgvector/HNSW over
   `document_chunks`, surfaced through the **Ask** feature (hybrid vector +
   FTS). The plain document list/search is FTS-only.

## 3. Key decisions

1. One unified "Edit layout" toggle (not two separate modes).
2. Layout persisted per-user in localStorage, applied to all documents (no
   backend/DB change) — mirrors the existing chart-controls pattern.
3. Hero customization is show/hide + reorder only; value editing stays in the
   Details editor.
4. Chart click UX = "both": single-doc bars click-through; grouped buckets get a
   sticky HTML tooltip with a link per document.
5. Doc-detail chart controls use *separate* storage keys from `/charts` (same
   defaults, independent state) so adjusting the trend doesn't move the
   dashboard.

## 4. Issues found and fixed during development

1. **`vue-tsc --build` caught a type error the unit run didn't.** W1's new test
   built a `IngestionEvent[]` from `EVENTS[i]` indexing, which is
   `IngestionEvent | undefined` under `noUncheckedIndexedAccess`. `vitest` and
   `vue-tsc --noEmit` passed; the CI `type-check` (`--build`) failed. Fixed with
   non-null assertions. Lesson: the CI type-check is stricter than `--noEmit` —
   always run `npm run type-check`, not just the tests.
2. **Edit-mode singleton persisted across SPA navigation (code review, Medium).**
   `useDocumentLayout.editMode` is module-singleton state that survives
   component unmount (it only resets on a full page reload). Entering edit mode,
   navigating away, and returning rendered the edit affordances with `editMode`
   still true but no Sortable instances attached (the `watch` only fires on a
   *change*), so dragging was silently dead until toggling off/on. Fixed by
   resetting `editMode` to false in the view's `onBeforeUnmount`; added a
   regression test asserting the flag clears on unmount.

## 5. Testing & verification

- Frontend unit suite (Vitest): **788 passed** (was 747 at session start; +41
  across the six units + regression test). Coverage 90.6% stmts / 92.7% lines.
- `vue-tsc --build` type-check: clean. ESLint: clean. Production `vite build`:
  succeeds.
- Verification was via the automated gate plus component tests that mount the
  real `DocumentDetailView` and assert rendered DOM (recipient row appears,
  toggles/handles reveal in edit mode, cards reorder). A live end-to-end
  click-through against a running backend (FastAPI + pgvector + embedder) was
  **not** performed — the changes are purely frontend view logic already covered
  by build + typed component tests.

## 6. Follow-ups / notes

1. Cross-column card moves are out of scope for v1 (within-column only).
2. `SeriesChartTile` bar-click + sticky tooltip is intentionally shared across
   all three chart surfaces — future chart work inherits it.
3. Playwright e2e for the drag/reorder was deliberately skipped: the doc-detail
   specs run on mobile/tablet-below-`lg` projects where drag-handle visibility
   assertions are the known-flaky trap. Edit-only controls use `v-if` (not
   `v-show`) so they don't break existing visibility assertions when off. A
   desktop-only smoke could be added later if wanted.
