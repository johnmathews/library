# Split the Details card into per-section metadata tiles

**Date:** 2026-07-09

## 1. What & why

On `/documents/:id` the left column showed a single **Details** card
(`DocumentMetadataEditor.vue`) whose contents were internally divided into
themed groups (Content, Classification, Sender & dates, Financial, plus
read-only Topics and System). It worked but "didn't sit quite natural." We
promoted each section to a **first-class tile** so the page reads as a set of
focused cards rather than one dense card-within-a-card.

Two decisions were locked with the user up front:

1. **One page-level Edit toggle** (not per-tile) — a single control flips every
   metadata tile into edit mode together, preserving the old
   edit-everything-at-once feel.
2. **All groups become tiles** — Content, Classification, Sender & dates,
   Financial, System. Topics folds into Content (its "aboutness"); System is its
   own read-only provenance tile.

## 2. What made it cheap

Two pieces of existing infrastructure did most of the work:

- The two-column grid already renders each card via a shared
  `DefineCard`/`ReuseCard` template driven by `useDocumentLayout`'s persisted
  `cardColumns`, with cross-column drag-reorder. Adding tiles = adding card ids.
- `useMetadataEditMode()` was **already** a shared singleton (the old Details
  toggle and the Action dock both drove it). So splitting the card didn't
  fragment the edit toggle — every tile just reads the same flag; we only moved
  the single visible toggle from the (removed) Details header into the hero,
  keeping its `edit-toggle` testid so the e2e specs were unaffected.

## 3. Approach

- **`DocumentMetadataEditor.vue`** gained a `section` prop and renders exactly
  one section as a standalone `.card`. The heavy per-field body (validation,
  autosave, kind/sender/recipient adders) is unchanged — it's driven by
  `v-for="group in activeGroups"` (a 0-or-1 array), so the same body now emits
  for one group. The view mounts five instances; each edits a disjoint field set
  bound to the shared `v-model:doc`. `onMounted` taxonomy fetches are guarded by
  section so five instances don't fire the list endpoints five times each.
- **`cardPresent`** gained rules for the five ids: Content and System always
  show; Classification / Sender & dates / Financial appear only when populated or
  while editing — so an empty **Financial** tile vanishes on a non-financial
  document (the concrete "unnatural" case) and reappears to be filled in on edit.
- A tile hidden in read mode first mounts only once editing is already on, so its
  `watch(editMode)` never fires — added a mount-time `hydrateDrafts()` when edit
  mode is already active.
- **Migration:** `migrateMetadataCard` expands a saved layout's lone `metadata`
  id **in place** into the five `metadata-*` ids before reconciliation, so a user
  who moved the Details card keeps its position instead of the new tiles being
  appended at the column's end.

## 4. Verification

`npm run test:unit` (907), `type-check`, `lint`, and `build` all green. Tests
extended: layout defaults + in-place migration; the editor spec now mounts per
section and flips the shared flag (the toggle left the tile); the view spec
covers the five tiles, Topics-in-Content, empty-tile hiding + reveal-on-edit, and
cross-column drag around the new ids. The real-stack e2e specs
(`library`, `projects`, `topics-readonly`) were reviewed as compatible — the
`edit-toggle`, `row-topics`, `topic-badge`, and `Status` hooks they depend on are
all preserved. The live compose stack was **not** driven in this session (needs
the backend + auth + LLM keys); CI's e2e job is the real-stack gate.

## 5. Follow-ups / open questions

- The five-instance mount means five `DocumentMetadataEditor` script instances.
  Cheap (taxonomy fetches guarded; project options cached), but if the page ever
  feels heavy, extracting a shared `MetadataField` child is the next step.
- Worth a look on the live deploy to confirm the accent-heading tiles read as
  intended visually (the user's original concern was aesthetic).
