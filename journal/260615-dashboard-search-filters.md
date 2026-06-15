# Dashboard search & filter bar

**Date:** 2026-06-15

## Goal

Add an always-visible search and filter bar to the document dashboard, so users
can search and filter without having to open the navbar `SearchModal`. The modal
stays — it is the intended filter surface on small screens — but on desktop the
bar gives a faster, zero-click path to every filter.

## Design decision: A+B hybrid

The spec ([`docs/superpowers/specs/2026-06-15-dashboard-search-filters-design.md`](../docs/superpowers/specs/2026-06-15-dashboard-search-filters-design.md))
considered three approaches: replacing the modal entirely (A), an always-visible
inline bar (B), and a hybrid that keeps both. We went with the hybrid: the modal
is unchanged and continues to work as before; the filter bar is additive. This
avoids breaking the `/`-shortcut flow, avoids duplicating test coverage, and
keeps mobile unchanged (the bar is not shown on small screens).

## URL as source of truth — shared `documentQuery` util

The central architectural choice was to keep the URL as the single source of
truth and centralise all reading/writing of query parameters in a new pure
helper module, `frontend/src/utils/documentQuery.ts`. It exports:

- `parseDocumentQuery(query)` — converts a `LocationQuery` into an
  `AppliedFilters` object (typed, defaults for every field).
- `buildDocumentQuery(filters)` — converts `AppliedFilters` back into a
  `LocationQueryRaw` ready for `router.push`/`router.replace`.
- `hasActiveFilters(filters)` — returns `true` if anything beyond defaults is
  set.

Both `DocumentListView` and `DocumentFilterBar` consume these helpers, so the
modal and the bar are naturally kept in sync: any write from either surface is
just a URL change, and both read from the same parsed route query.

## What was built

- **`frontend/src/utils/documentQuery.ts`** — the shared URL-query ⇆
  applied-state helpers + the `AppliedFilters` type.
- **`frontend/src/components/app/FilterPill.vue`** — a new reusable controlled
  popover primitive: a rounded button that toggles a slotted dropdown panel,
  `v-model:open`, closes on Escape or outside mousedown. Exported from the
  `@/components/app` barrel.
- **`frontend/src/components/DocumentFilterBar.vue`** — the dashboard hero bar:
  debounced (300 ms) search input, filter pills (Kind, Sender, Date range, Tag
  [multi-select], and a More pill for Language + Status), and removable
  active-filter chips with "Clear all". Fully controlled: receives `applied`,
  emits the next URL query.
- **`frontend/src/views/DocumentListView.vue`** — renders `DocumentFilterBar`
  in the hero and applies its emitted query via `router.push` (or
  `router.replace` for debounced typing). The old plain-text "Filtered by …"
  summary line was removed.
- **`frontend/src/api/documents.ts`** — added `DOCUMENT_STATUSES` options array.

## Multi-tag and status

Two filter additions that required changes beyond the bar itself:

- **Multi-tag:** tags are now repeating URL params (`?tag=a&tag=b`),
  `AppliedFilters.tags` is `string[]`. The backend API already supported
  ANDed multi-tag queries; this was a frontend-only gap.
- **Status filter:** new `status` field in `AppliedFilters`, backed by
  `DOCUMENT_STATUSES` in `src/api/documents.ts`. Allows filtering to e.g.
  only `indexed` or only `failed` documents.

## Modal kept as mobile surface

`SearchModal` and its `DefaultLayout` wiring are unchanged. The navbar search
button and the `/` keyboard shortcut continue to work exactly as before. The
filter bar is not rendered on small viewports where the modal remains the
natural entry point.
