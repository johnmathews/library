# Document-type quick filters & detail prev/next navigation

**Date:** 2026-07-16

Two frontend-only features, run through the engineering-team cycle (scoped
evaluation → plan → TDD build). Run dir:
`.engineering-team/runs/manual-20260716T163824Z/`.

## 1. Document-type quick-filter pills

A second pill row on the dashboard filter bar
(`DocumentFilterBar.vue`), directly below the Kind/Sender/Recipient row
(`[data-testid="type-filters"]`). One pill per kind that has documents,
ordered most-numerous first.

- Ordering/labels come straight from the already-cached
  `GET /api/kinds` `document_count` (global, excludes deleted) — **no backend
  work**. Ties break by name for a stable order.
- Zero-count kinds are dropped (a quick-filter to an empty result set is noise).
- **Follow-up (2026-07-17):** on mobile the pills wrapped to ~4 rows and the
  catch-all "Other" led the list. Changed the row to a **single sideways-scrolling
  line** (`overflow-x-auto whitespace-nowrap`, pills `shrink-0`) and **pinned
  `other` last** regardless of count.
- Each pill is a shortcut to the single-value **Kind** filter: click applies
  `?kind=<slug>` via the existing `selectKind()`; clicking the active pill
  clears it (`toggleKind`). Active state is violet + `aria-pressed`.

## 2. Previous/next document navigation

On `/documents/:id`, below the "Back to documents" link and above the hero
(`[data-testid="doc-neighbors"]`, `doc-prev` / `doc-next`).

- New composable `useDocumentNeighbors(currentId)`. There is **no server
  neighbour endpoint** and the list view keeps results in component-local state,
  so neighbours are computed client-side.
- **Decision (with the user):** walk the user's **remembered sort**
  (`localStorage['library:doc-sort-v1']`, default added-date desc),
  **unfiltered**. Self-contained — works on a cold deep-link/refresh — over
  matching whatever filters the list happened to have. Filtered prev/next was
  explicitly deferred.
- It paginates `GET /api/documents` (100/page, capped at 20 pages / 2000 docs)
  until it finds the current id and reads the ids either side. Degrades to
  no-neighbours on a fetch error; hides a direction at the list ends.
- The bar is hidden in **review-queue** mode (the queue bar owns navigation)
  and for **trashed** documents (excluded from the list → no neighbours).

## 3. Verification

- New/updated specs: `DocumentFilterBar.spec.ts` (+3),
  `useDocumentNeighbors.spec.ts` (new, 7), `DocumentDetailView.spec.ts` (+6).
- Full frontend suite green: **980 tests / 84 files**. `vue-tsc` type-check and
  `eslint` clean. No Python touched.

## 4. Files

- `frontend/src/components/DocumentFilterBar.vue` — `typeFilters` computed,
  `toggleKind`, pill row.
- `frontend/src/composables/useDocumentNeighbors.ts` — new.
- `frontend/src/views/DocumentDetailView.vue` — nav bar + wiring.
- `docs/frontend.md` — filter-bar and detail-view sections updated.
