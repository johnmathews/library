# Dashboard inline search & filters — design

**Date:** 2026-06-15
**Status:** draft

## Problem

The only way to search or filter the document library is a small magnifying-glass
icon in the top nav bar that opens `SearchModal.vue`. Search is hidden behind two
clicks and a dialog, and there is no at-a-glance way to see or adjust which
filters are active from the dashboard itself. The dashboard hero is just an
`<h1>Documents</h1>` with a count.

The backend already fully supports the filtering we want: `GET /api/documents`
accepts `q`, `kind`, `sender_id`, `tag` (repeatable), `language`, `status`,
`date_from`, `date_to`, `source`, `limit`, `offset`. This is a **frontend-only**
feature — no API or schema changes.

## Goal

Surface search and filtering directly in the dashboard hero as an always-visible
filter bar, using the **A+B hybrid** direction approved in brainstorming:

1. A prominent **search input** at the top of the content area, below the
   "Documents" title.
2. A row of **dropdown filter pills** — Kind, Sender, Date, Tag, and a "More"
   pill (Language + Status). Pills light up and show their chosen value when
   active.
3. A row of **removable active-filter chips** (one per applied filter, including
   the search text), with a **Clear all** control.

The URL query string stays the single source of truth, so the bar, the existing
nav-bar `SearchModal`, the browser back/forward buttons, and shareable links all
stay in sync. The nav-bar search icon and modal remain and keep working.

## Approach

Extract the new UI into a dedicated **`DocumentFilterBar.vue`** component rendered
in the dashboard hero, plus a small reusable **`FilterPill.vue`** popover
primitive (button + dropdown panel) that the app does not currently have. The
existing `DocumentListView.vue` keeps owning URL ⇆ fetch state; the filter bar is
a controlled component that reads applied state via props and requests changes via
events (or, more simply, reads the route and pushes new queries itself — see
"State ownership" below).

Two URL-handling extensions are required because the current code is narrower than
the API:

- **Tag becomes multi-select.** Today both the modal and list view treat `tag` as
  a single string. The API already accepts repeated `?tag=a&tag=b`. Parsing must
  accept `tag` as `string | string[]` and normalise to `string[]`; query building
  must emit one `tag` entry per selected tag.
- **Status becomes filterable from the UI.** `status` is supported by the API but
  not currently parsed into `applied` or offered in the modal. Add it to the
  "More" pill.

### State ownership

`DocumentListView.vue` remains the owner of the applied-state computed and the
fetch watcher. `DocumentFilterBar` is given:

- `:applied` — the parsed applied-state object (props in).
- It emits the **new `LocationQueryRaw`** to apply; the parent calls
  `router.push` / `router.replace`. This keeps all routing in one place and the
  bar pure/testable.

Resetting any filter or the search text **resets `page` to 1**.

### Search input behaviour

- **Live, debounced.** Typing updates the `q` query param after a ~300 ms debounce
  via `router.replace` (not `push`) so rapid typing does not flood browser
  history. Pressing **Enter** applies immediately. A clear (✕) button empties it.
- The input is initialised from `applied.q` and stays reconciled if the query
  changes from elsewhere (e.g. the modal or a chip removal).

### Filter pills

A `FilterPill` renders a pill-styled button that toggles a popover panel anchored
below it. Behaviour shared by all pills: click-outside closes, `Escape` closes and
returns focus to the button, only one popover open at a time. Modelled on the
existing header user-menu dropdown for focus/keyboard consistency.

| Pill     | Param(s)              | Control inside popover                       | Select |
| -------- | --------------------- | -------------------------------------------- | ------ |
| Kind     | `kind`                | option list (reuse kind taxonomy)            | single |
| Sender   | `sender_id`           | option list, searchable if long              | single |
| Date     | `date_from`/`date_to` | two `AppDateInput`s (from / to)              | range  |
| Tag      | `tag` (repeatable)    | `AppCheckboxes` over tag taxonomy            | multi  |
| More     | `language`, `status`  | two grouped option lists                     | single each |

- An **active** pill shows its value inline (`Kind: Invoice`, `Tag: Family +1`)
  and uses the accent styling; an inactive pill shows just its label.
- Applying a pill change pushes a new query (discrete action → `router.push`).
- Taxonomy options (kinds, senders, tags) come from the existing
  `useTaxonomyOptions()` composable, fetched lazily via `ensureLoaded()` when the
  bar mounts.

### Active-filter chips

Below the pill row, render one removable chip per applied filter, reusing the
existing `filterSummary` resolution logic (slug/id → display name) generalised to
return structured `{ key, label, onRemove }` entries instead of strings. Each chip
has a ✕ that removes just that filter (for tags, removes that one tag). A **Clear
all** control clears every filter (reuses the existing `clearFilters()` →
`router.push({ query: {} })`). When nothing is applied, the chip row is hidden.

The current text-only `filterSummary` line in `DocumentListView` is replaced by
these chips.

### Mobile

The pill row wraps on narrow screens. Below the `sm` breakpoint the individual
pills collapse behind a single **"Filters"** button that opens the existing
`SearchModal` (already responsive), avoiding a cramped popover experience on
phones. The search input remains full-width and inline. This reuses the modal we
are keeping rather than building a separate mobile sheet.

## Changes by layer

### 1. `frontend/src/api/documents.ts`

- `DocumentFilters` already types `tag` as `string[]` and includes
  `status?: DocumentStatus` (lines 86–99), so the API client needs no new fields.
- Add a `DOCUMENT_STATUSES` options array (`{ value, text }`) mirroring the
  existing `DOCUMENT_LANGUAGES` export, for the "More" pill's status list.

### 2. `frontend/src/views/DocumentListView.vue`

- Extend `applied` to parse `tag` as `string[]` (accept `string | string[]` from
  the query) and to parse `status`.
- Extend `buildQuery` to emit repeated `tag` and `status`.
- Replace the inline `filterSummary` string list / "Clear filters" markup with the
  new `<DocumentFilterBar>` placed in the hero (below the `<h1>`), passing
  `:applied` and handling its `@apply` / `@clear` events by pushing queries.
- Update the fetch watcher to pass `tag: applied.tag` (already an array) and
  `status` through to `listDocuments`.
- Keep pagination, tiles, and everything below unchanged.

### 3. `frontend/src/components/DocumentFilterBar.vue` (new)

- Props: `applied` (parsed state), taxonomy via composable.
- Emits: `apply(query: LocationQueryRaw)` and `clear()`.
- Renders: search input (debounced), pill row (`FilterPill` instances), chip row.
- Owns the debounce timer and the "which popover is open" state.

### 4. `frontend/src/components/app/FilterPill.vue` (new)

- A reusable button + popover. Props: `label`, `active`, `valueLabel?`. Slot for
  the panel contents. Handles open/close, click-outside, `Escape`, focus return.
- Lives under `components/app/` and is exported from its index, alongside
  `AppSelect` etc., since it is a generic primitive.

### 5. `frontend/src/components/app/index.ts`

- Export `FilterPill`.

## Testing

**Frontend** (existing Vitest component setup):

- `DocumentFilterBar`: typing in the search input emits an `apply` with `q` after
  the debounce; Enter applies immediately; clearing emits `q`-removed.
- Selecting a Kind / Sender emits the right single-value query; selecting multiple
  Tags emits repeated `tag`; choosing a Language/Status in "More" emits those.
- Active pills reflect applied state and show the value label; removing a chip
  emits a query without that one filter; a tag chip removes only its tag.
- "Clear all" emits an empty query.
- `FilterPill`: opens/closes on click, closes on `Escape` (focus returns to
  button) and on outside click; only one open at a time within a bar.
- `DocumentListView`: `applied` parses repeated `tag` into an array and parses
  `status`; `buildQuery` round-trips both; the fetch passes them to
  `listDocuments`. Existing list/pagination tests still pass.
- Backward-compat: a URL with a single `?tag=family` (old modal-style) still
  parses and fetches correctly.

## Out of scope

- Backend / API / DB changes (the API already supports every filter).
- `source` filter in the UI (supported by API; not in this bar — YAGNI for now).
- Saved searches / filter presets.
- Removing or redesigning the existing `SearchModal` (kept as the mobile filter
  surface and the `/`-shortcut entry point).
- Sorting controls, faceted result counts, or a left filter rail (rejected
  direction C).

## Docs to update on implementation

- `docs/frontend.md` — document the dashboard filter bar, the live-search
  behaviour, multi-select tags, and that the modal is now the mobile filter
  surface.
- A `/journal` entry per the project journal convention.
