# Free-form tile reorder and the configurable Action dock

Date: 2026-07-06

Two `/documents/:id` UX improvements, built via the full brainstorm → spec →
plan → subagent-driven-development cycle (6 tasks, per-task review, opus
whole-branch review) on branch `worktree-feat-freeform-cards-action-dock`.

## 1. What shipped

### 1.1 Free-form cross-column tile reorder
The "Edit layout" mode previously only reordered cards *within* a column (each
column was a separate SortableJS instance with static membership, so `comments`
was pinned to the metadata column). Now any tile can be dragged into either
column. `useDocumentLayout` replaced its flat `cardOrder` with a persisted
two-column model `cardColumns: { left, right }` (key
`library:doc-layout-card-columns-v1`); both column drag containers share a
SortableJS `group` so cards cross; `onEnd` reverts SortableJS's DOM move before
mutating the reactive state (so Vue re-renders the single source of truth), and a
present→full index mapping keeps drops correct when hidden cards precede the drop
point. Existing users' old flat `library:doc-layout-card-order-v1` migrates on
first load (order preserved, new cards appended) so nothing jumps.

### 1.2 The Action dock
The floating Edit+Ask "island" was renamed to the **Action dock** and extracted
into `ActionDock.vue`. It still appears once the hero scrolls off. Its on-screen
position is a per-user preference — one of `top-left | top-middle | top-right
(default) | bottom-left | bottom-right` — set in **Settings → Appearance → Action
dock position**. It renders in a content-anchored `sticky`, `pointer-events-none`
wrapper (top positions offset `top-16` below the sticky `h-16` header) so all five
positions stay below the navbar, within the content, never over the sidebar.

### 1.3 `dock_position` preference
Server-side in the JSONB `users.preferences` (no DB migration), mirroring the
`background_tone`/`tile_preview` pattern exactly: a `DockPosition` StrEnum,
`_resolve_dock_position` (unknown/missing → default), a before-validator, and
`PUT /api/settings/appearance` persistence. Frontend: `DOCK_POSITIONS` type,
`auth.dockPosition`, and `updateAppearance`'s 3rd param. Values are byte-identical
across the whole stack (enum ↔ TS literals ↔ dock switch ↔ settings buttons).

## 2. Key decisions

1. Fully free-form: any card into either column (not "pin the preview"), since
   both columns are equal width and it matches "rearrange the tiles".
2. Name: **Action dock** (was "island").
3. Keep the appear-once-hero-scrolls-off trigger; 5 positions; default top-right.
4. Store the dock position server-side in the existing appearance prefs (JSONB,
   no migration) so it syncs across devices — consistent with `background_tone`.

## 3. Issues caught in review (and fixed)

1. **IMPORTANT (opus per-task review): the legacy migration was dead code.**
   `useStorage(key, default)` writes the default to localStorage synchronously at
   construction, so the next line's `isFresh = !localStorage.getItem(key)` was
   *always false* → the flat-order migration never ran, and any user who had
   customized their card order would silently reset (cards jump). Fixed by
   capturing `hadColumns`/`legacyOrder` *before* the `useStorage` call; added a
   `vi.resetModules()` + seeded-legacy-key wiring test that reproduces it
   (RED→GREEN). It had slipped 812 green tests because nothing exercised the
   module-init block.
2. **MINOR (docs pass): stale in-app hint.** The Edit-layout hint still said
   "Cards below reorder within their column" — now wrong. Updated to reflect
   cross-column drag.
3. The whole-branch review verified (against the SortableJS 1.15.7 source) that
   the hero-field Sortable (no group) can't accidentally share the cards'
   `doc-cards` group, so hero reorder stays isolated.

## 4. Verification

- Backend `uv run pytest` → **956 passed**. Frontend `npm run test:unit` →
  **825 passed** (cov 90.9% stmts / 93.1% lines). `ruff format --check` +
  `ruff check` clean; eslint clean; `vue-tsc --build` clean; `vite build`
  succeeds. No DB migration (JSONB preference).
- **Not covered by tests (jsdom limitation):** the five dock CSS placements and
  real drag physics. The positioning is deterministic Tailwind class mapping with
  all five branches unit-exercised, and the drag math is unit-tested, but a
  ~2-minute real-browser smoke (set a `top-*` position → confirm it clears the
  header; do one cross-column card drag) is worth doing on the live site.

## 5. Follow-ups (non-blocking)

1. Cosmetic: leftover "island" wording in a few code comments / test names after
   the rename (`useMetadataEditMode.ts`, `DocumentMetadataEditor.vue`/`.spec`).
2. A seriesless chart shows as an empty draggable card in Edit-layout mode
   (pre-existing quirk from when cards first became draggable, not new here).
