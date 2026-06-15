# Dashboard search & filter bar

**Date:** 2026-06-15

## Goal

Add an always-visible search and filter bar to the document dashboard, so users
can search and filter without having to open the navbar `SearchModal`. The modal
stays as a secondary entry point (and the `/`-shortcut surface); the bar gives a
faster, zero-click path to every filter.

## Design decision: A+B hybrid

The spec ([`docs/superpowers/specs/2026-06-15-dashboard-search-filters-design.md`](../docs/superpowers/specs/2026-06-15-dashboard-search-filters-design.md))
considered three approaches: replacing the modal entirely (A), an always-visible
inline bar (B), and a hybrid that keeps both. We went with the hybrid: the modal
is unchanged and continues to work as before; the filter bar is additive. This
avoids breaking the `/`-shortcut flow and avoids duplicating test coverage. The
bar renders at all viewport sizes. Below the `sm` breakpoint the pill row
collapses behind a "Filters" toggle (with an active-filter count badge) that
expands the pills inline — chosen over routing mobile users to the modal, since
the modal is a reduced surface (no status, single-tag); the inline disclosure
keeps full filter parity on mobile.

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

## Modal kept, made non-lossy

`SearchModal`'s open/close, focus, and `/`-shortcut behaviour are unchanged, but
a final integration review caught a cross-cutting bug: the modal rebuilt the
whole URL query from its own draft on submit, so it **silently dropped** the
extra tags (its tag `<select>` is single-value) and the `status` filter set via
the bar. Fixed by migrating the modal onto `parseDocumentQuery`/
`buildDocumentQuery` and preserving state it can't represent — multiple tags are
kept when the tag field is untouched, and `status` is always carried through.
Redesigning the modal to add multi-tag UI was kept out of scope.

## Process & delivery

Built via subagent-driven development: each of the 7 planned tasks (plus the
non-lossy modal fix surfaced by the final whole-branch review) was implemented
by a fresh subagent and passed a two-stage spec-compliance + code-quality review.
Reviews caught and fixed real issues along the way — `FilterPill` ARIA
(`aria-haspopup`/`aria-pressed` removed), a multi-tag accumulation bug in the bar
(local synced ref), Kind/Sender in-pill reset options, and the modal data-loss
fix above. Final state: 215 frontend tests passing, eslint + `vue-tsc` clean.

Merged to `main`, CI built and promoted `ghcr.io/johnmathews/library:latest`, and
deployed to the `paperless` LXC (`docker compose up -d --pull always` for the
three `library-*` services). Verified live: `/api/settings` → 401 (auth-gated new
build) and a fresh `index-C00yG34k.js` bundle served at
`http://192.168.2.117:8010`.
