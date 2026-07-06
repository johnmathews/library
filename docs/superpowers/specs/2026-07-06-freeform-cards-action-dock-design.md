# Free-form tile reorder and the configurable Action dock

**Status:** active. **Last updated:** 2026-07-06. **Supersedes:** none.

Two related `/documents/:id` UX improvements: (A) let tiles be dragged freely
between both columns (today's reorder is within-column only), and (B) rename the
floating "island" to the **Action dock**, keep its appear-on-scroll behaviour,
and make its position a per-user Appearance setting.

## 1. Scope and goals

1. **Free-form cross-column tile reorder** — any card can be dragged into either
   column, in any order, in "Edit layout" mode. Fixes the current limitation
   where `comments` (and every metadata-column card) is pinned to its column.
2. **Action dock** — the floating Edit+Ask control gets a name, a configurable
   position (5 options, default top-right), stored per-user in the Appearance
   settings, and repositioned to stay within the main content area.

Non-goals: no change to hero-field reorder (single list, unaffected); no change
to what the dock *does* (Ask + metadata Edit/Done); no new document data.

## 2. Current state (grounding)

- Card layout lives in `useDocumentLayout` (`frontend/src/composables/useDocumentLayout.ts`):
  a flat `cardOrder: string[]` persisted at `library:doc-layout-card-order-v1`.
  The *view* (`DocumentDetailView.vue`) owns the column split via static
  constants `PREVIEW_CARD_IDS = ['preview','markdown','series-chart']` (right,
  `lg:order-2`) and `METADATA_CARD_IDS = ['notes','metadata','comments','actions','history']`
  (left, `lg:order-1`), filtering `cardOrder` into `previewCards`/`metadataCards`.
- Reorder uses two separate `Sortable.create` instances (`previewColumnEl`,
  `metadataColumnEl`) with **no shared group**, so a card cannot cross columns
  (`reorderCards(present, oldIndex, newIndex)` only permutes within one column).
- The "island" is inline in `DocumentDetailView.vue` (`data-testid="detail-island"`,
  `island-ask`, `island-edit-toggle`), `position: fixed bottom-4 right-4 z-40`,
  shown when `heroVisible` is false (IntersectionObserver on `#document-hero`).
- App shell (`frontend/src/layouts/DefaultLayout.vue`): `#app-shell` is a flex row
  of `AppSidebar` + `#app-content` (`relative`, `overflow-y-auto` — the scroll
  container), which holds `AppHeader` then `#app-main > #app-page`. The sidebar is
  a **sibling** of the content area, so content-positioned elements never overlap
  it.
- Settings: `/settings` → `SettingsView.vue`, tabs `dashboard | appearance |
  notifications`. The **Appearance** tab already persists per-user, server-side
  prefs (page-canvas tone, tile preview, per-kind colours) via `PUT
  /api/settings/appearance` (`updateAppearance`), auto-saving per change with an
  optimistic store update. `users.preferences` is a JSONB column
  (`models.py:143`), so new preference keys need **no DB migration**. The auth
  store exposes `backgroundTone` as a computed off `user.preferences` — the model
  to mirror for `dockPosition`.

## 3. Feature A — Free-form cross-column tile reorder

### 3.1 Layout model

Replace the flat `cardOrder` (+ the view's static column constants) with a
persisted **two-column** model in `useDocumentLayout`:

```ts
interface CardColumns { left: string[]; right: string[] }   // left = metadata col, right = preview col
```

- New storage key `library:doc-layout-card-columns-v1`.
- `DEFAULT_CARD_COLUMNS`: `left: ['notes','metadata','comments','actions','history']`,
  `right: ['preview','markdown','series-chart']` — identical to today's rendered
  layout, so nothing moves on first load.
- **Migration from the old flat key:** on first read, if the new key is absent but
  the old `library:doc-layout-card-order-v1` exists, split that flat order into
  the two columns using the *old* static membership (preview ids → right,
  metadata ids → left), preserving each card's relative order. (Keep this as a
  pure, tested helper.)
- **Merge-safe reconcile** (`reconcileCardColumns(stored, defaults)`): every known
  card id appears in exactly one column; a known card missing from stored state
  is appended to its default column; a stored id not in the defaults is dropped;
  a card that somehow appears in both columns is de-duplicated (keep first). The
  card set is the union of both default columns.
- Mutators: `moveCard(cardId, toColumn, toIndex)` (removes from whichever column
  holds it, inserts into `toColumn` at `toIndex`); `setColumn(column, ids)`.
  `resetLayout()` restores `DEFAULT_CARD_COLUMNS` (and the hero-field defaults, as
  today).

The `editMode` flag and hero-field state are unchanged.

### 3.2 Rendering + drag

- `DocumentDetailView.vue` renders each column by `v-for` over
  `useDocumentLayout().cardColumns.left` / `.right` (filtered by `cardPresent`),
  dropping the static `PREVIEW_CARD_IDS`/`METADATA_CARD_IDS` constants and the
  derived `previewCards`/`metadataCards`. Each column container carries a stable
  `data-col="left"|"right"`.
- Both column Sortables get a **shared `group: 'doc-cards'`** so a card can be
  dragged from one column into the other. The hero-field Sortable is untouched.
- **`onEnd` handler** (replaces `reorderCards`): read `evt.from`/`evt.to`'s
  `data-col` and `evt.oldIndex`/`evt.newIndex`; the moved card id =
  `sourceColumn[oldIndex]`; call `moveCard(id, destCol, newIndex)`. **Cross-list
  DOM/Vue reconciliation:** because SortableJS physically moves the `<li>` into
  the destination list while Vue will also re-render both lists from the reactive
  arrays, revert SortableJS's DOM mutation before mutating state — in `onEnd`,
  put the node back where it came from (`evt.from.insertBefore(evt.item,
  evt.from.children[evt.oldIndex])`) and then call `moveCard(...)`, letting Vue
  render the single source of truth. Keyed `v-for` (`:key="cardId"`) stays.
  *(Fallback if the manual revert proves flaky in review: adopt `vuedraggable`'s
  `<draggable v-model>` for the two columns, which handles cross-list sync
  natively — a larger change, chosen only if needed.)*
- Cards remain wrapped in `section-card-{id}` with their drag handle, unchanged.

### 3.3 Responsive + present-card behaviour

Below `lg` the two columns stack (as today); the shared-group drag still works
between the stacked containers. Cards not present for a document (`cardPresent`,
e.g. `notes` on non-note docs, a seless `series-chart`) are simply not rendered —
they stay in their column list but produce no DOM, so a drag never targets them.

### 3.4 Tests (A)

- `reconcileCardColumns`: appends a missing known card to its default column;
  drops an unknown stored id; de-dupes a card present in both; preserves saved
  order.
- Migration helper: an old flat `cardOrder` splits into the correct two columns
  preserving order.
- Component: entering edit mode, a card id moved from left→right (drive the
  `onEnd` with a stubbed Sortable event) ends up in `cardColumns.right` at the
  target index and renders in the right column; default layout unchanged;
  `resetLayout` restores defaults.

## 4. Feature B — Action dock

### 4.1 Rename + extraction

Extract the inline island into `frontend/src/components/ActionDock.vue`
(props: `documentId`/`askHref` + whatever it needs; consumes
`useMetadataEditMode`). User-facing name is **Action dock** (aria-label,
tooltip, settings label, docs). Rename testids to `action-dock`,
`action-dock-ask`, `action-dock-edit-toggle` (update the existing island tests).
Keep the appear-once-`#document-hero`-scrolls-off trigger (the IntersectionObserver
stays in `DocumentDetailView`, which conditionally renders `<ActionDock>` when
`!heroVisible`).

### 4.2 Preference (backend, no migration)

Add `dock_position` to the appearance preferences model
(`AppearancePreferences` in `src/library/schemas.py` or wherever the appearance
block lives — mirror `background_tone`): a string enum
`top-left | top-middle | top-right | bottom-left | bottom-right`, default
`top-right`. Thread through:
- the appearance Pydantic model + `resolve_preferences` default,
- `PUT /api/settings/appearance` (accept + persist it into the JSONB
  `users.preferences`),
- validation: reject an unknown value (422) — a closed enum.

No Alembic migration (JSONB). Existing users without the key resolve to
`top-right`.

### 4.3 Preference (frontend)

- `frontend/src/api/settings.ts`: add `dock_position` to the appearance
  types + the `updateAppearance` write body.
- `frontend/src/stores/auth.ts`: add a `dockPosition` computed off
  `user.preferences` (default `top-right`), mirroring `backgroundTone`.
- `SettingsView.vue` Appearance tab: an "Action dock position" control — 5
  buttons (a small 3×2-ish grid or a labelled segmented control) that call
  `updateAppearance({... , dock_position})` on click, optimistic store update +
  the same error toast pattern as the tone swatches. Stable testids
  (`settings-dock-position`, `dock-position-{value}`).

### 4.4 Positioning

`ActionDock` reads `auth.dockPosition` and renders inside a **content-width,
`pointer-events-none`, `position: sticky`** wrapper:
- top-* → sticky to the top (offset below the header); bottom-* → sticky to the
  bottom.
- horizontal: `justify-start` (left), `justify-center` (middle), `justify-end`
  (right).
- the dock element itself is `pointer-events-auto` (so the transparent wrapper
  never blocks content).

Because the wrapper sits in the `#document-detail` page flow inside `#app-content`
(a sibling of the sidebar), it is automatically below the header, within the main
content, and never over the sidebar — for all five positions, at every width.
Keep `z-40`. Default top-right.

### 4.5 Tests (B)

- `ActionDock`: for each of the 5 `dockPosition` values, asserts the wrapper
  carries the expected sticky-edge + justify classes; the dock renders the Ask
  anchor + Edit toggle with the renamed testids; Edit toggles
  `useMetadataEditMode`.
- `DocumentDetailView`: dock absent while hero intersects, present once it
  doesn't (existing test, retargeted to the new testid/component).
- `SettingsView`: the dock-position control calls `updateAppearance` with the
  chosen value and updates the store optimistically.
- Backend: appearance settings round-trips `dock_position`; unknown value → 422;
  missing key resolves to `top-right`.

## 5. Work breakdown (for the plan)

1. **A1** — `useDocumentLayout` two-column model + migration + reconcile + tests.
2. **A2** — `DocumentDetailView` renders from `cardColumns`, shared-group
   cross-column drag with DOM-revert, tests.
3. **B1** — backend `dock_position` appearance preference (model + resolve +
   PUT + validation) + tests.
4. **B2** — frontend preference plumbing: settings API types, `auth.dockPosition`.
5. **B3** — extract `ActionDock.vue` (rename, testids) + positioning from
   `dockPosition`, mount in `DocumentDetailView`, tests.
6. **B4** — Appearance-tab "Action dock position" control + tests.
7. **Docs** — `docs/frontend.md` (free-form columns, Action dock + setting),
   `docs/api.md` (appearance `dock_position`).

## 6. Cross-cutting notes

- Frontend: Vitest, `vue-tsc --build` (noUncheckedIndexedAccess on — guard
  indexing), eslint, `vite build`. Gate edit-only/floating UI with `v-if`.
- Backend: Python 3.13, `uv`, pytest; ruff over the whole repo.
- Keep the "Reset layout" affordance working against the new two-column model.
- No per-user authorization change; appearance prefs are the user's own (see
  the app's shared-library authorization model, architecture.md §1.5.1).
