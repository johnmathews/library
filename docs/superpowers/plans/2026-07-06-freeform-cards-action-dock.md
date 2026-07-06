# Free-form Tile Reorder and Configurable Action Dock Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `/documents/:id` tiles be dragged freely between both columns, and turn the floating "island" into a named, repositionable **Action dock** with a per-user Appearance setting.

**Architecture:** Replace `useDocumentLayout`'s flat `cardOrder` (+ the view's static column constants) with a persisted two-column model `{left, right}` and a shared SortableJS drag group so cards cross columns. Add a `dock_position` key to the JSONB appearance preferences (no DB migration), edited in the Appearance settings tab, and extract the dock into `ActionDock.vue` positioned via a content-anchored sticky wrapper.

**Tech Stack:** Vue 3 + TypeScript + Pinia + Tailwind v4 + SortableJS (frontend, Vitest); FastAPI + Pydantic (backend, Python 3.13, `uv`, pytest).

## Global Constraints

- Frontend: `vue-tsc --build` type-check must pass (`noUncheckedIndexedAccess` on — guard array indexing with `!`/checks); eslint clean; `vite build` succeeds; no `any`. Gate edit-only/floating UI with `v-if` not `v-show`.
- Backend: Python 3.13; `uv`; pytest; type annotations on all signatures; ruff clean repo-wide (`ruff format --check`, `ruff check`).
- `dock_position` values (closed enum): `top-left | top-middle | top-right | bottom-left | bottom-right`. Default `top-right`.
- NO Alembic migration — `users.preferences` is JSONB; the new key resolves to `top-right` when absent (mirror `background_tone`'s before-validator + `_resolve_*` default pattern).
- Default two-column layout must equal today's rendered layout: left = `notes, metadata, comments, actions, history`; right = `preview, markdown, series-chart`.
- Commit after each task's tests pass.

---

## File Structure

**Frontend**
- Modify `frontend/src/composables/useDocumentLayout.ts` — two-column model + migration + reconcile (A1).
- Modify `frontend/src/views/DocumentDetailView.vue` — render from `cardColumns`, shared-group cross-column drag, mount `ActionDock` (A2, B3).
- Create `frontend/src/components/ActionDock.vue` — the extracted, positioned dock (B3).
- Modify `frontend/src/api/settings.ts` — `dock_position` type + `updateAppearance` param (B2).
- Modify `frontend/src/stores/auth.ts` — `dockPosition` computed (B2).
- Modify `frontend/src/views/SettingsView.vue` — Appearance "Action dock position" control (B4).

**Backend**
- Modify `src/library/schemas.py` — `DockPosition` enum, `_resolve_dock_position`, `dock_position` on `AppearancePreferences` + resolved prefs (B1).
- Modify `src/library/api/settings.py` — persist `dock_position` in `put_appearance` (B1).

**Docs**
- Modify `docs/frontend.md`, `docs/api.md` (Docs task).

---

## Task 1 (A): Free-form cross-column tile reorder

This task is one deliverable in two parts — the composable model (Part 1) and its
only consumer, the view (Part 2). Land them together; the tree must be green
(tests + type-check) at the end of the task.

### Part 1 — Two-column layout model in `useDocumentLayout`

**Files:**
- Modify: `frontend/src/composables/useDocumentLayout.ts`
- Test: `frontend/src/composables/__tests__/useDocumentLayout.spec.ts`

**Interfaces:**
- Produces: `interface CardColumns { left: string[]; right: string[] }`; `DEFAULT_CARD_COLUMNS`; `CARD_COLUMNS_STORAGE_KEY = 'library:doc-layout-card-columns-v1'`; `reconcileCardColumns(stored, defaults): CardColumns`; `migrateCardOrderToColumns(flatOrder: readonly string[]): CardColumns`; and on the `DocumentLayout` return: `cardColumns: Ref<CardColumns>`, `moveCard(cardId: string, toColumn: 'left'|'right', toIndex: number): void`, `setColumn(column: 'left'|'right', ids: readonly string[]): void`. `resetLayout()` also resets `cardColumns` to `DEFAULT_CARD_COLUMNS`.
- Note: the existing flat `cardOrder`/`setCardOrder`/`reconcileCardOrder`/`CARD_ORDER_STORAGE_KEY` are REMOVED and replaced by the column model. The old `moveCard(fromIndex, toIndex)` signature is replaced by the new one above (A2 is the only consumer and is updated in the same feature).

- [ ] **Step 1: Write failing tests**

Add to `useDocumentLayout.spec.ts` (mirror the file's existing reset/localStorage `beforeEach`):

```ts
import {
  reconcileCardColumns, migrateCardOrderToColumns, DEFAULT_CARD_COLUMNS, useDocumentLayout,
} from '@/composables/useDocumentLayout'

it('reconcileCardColumns appends missing known cards, drops unknown, de-dupes', () => {
  const stored = { left: ['metadata', 'metadata', 'ghost'], right: ['preview'] } // dup + unknown, missing several
  const merged = reconcileCardColumns(stored, DEFAULT_CARD_COLUMNS)
  const all = [...merged.left, ...merged.right]
  expect(all).not.toContain('ghost')                         // unknown dropped
  expect(all.filter((c) => c === 'metadata')).toHaveLength(1) // de-duped
  expect(new Set(all)).toEqual(new Set([...DEFAULT_CARD_COLUMNS.left, ...DEFAULT_CARD_COLUMNS.right])) // every known card present once
  expect(merged.left[0]).toBe('metadata')                    // preserved stored order for survivors
})

it('migrates an old flat card order into the two default columns preserving order', () => {
  const flat = ['history', 'markdown', 'metadata', 'preview'] // mixed columns, custom order
  const cols = migrateCardOrderToColumns(flat)
  expect(cols.left).toEqual(['history', 'metadata'])   // metadata-column ids, in flat order
  expect(cols.right).toEqual(['markdown', 'preview'])  // preview-column ids, in flat order
})

it('moveCard moves a card across columns to the target index', () => {
  const layout = useDocumentLayout()
  layout.resetLayout()
  layout.moveCard('comments', 'right', 1)
  expect(layout.cardColumns.value.left).not.toContain('comments')
  expect(layout.cardColumns.value.right[1]).toBe('comments')
})
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run src/composables/__tests__/useDocumentLayout.spec.ts`
Expected: FAIL (exports undefined).

- [ ] **Step 3: Implement the model**

In `useDocumentLayout.ts`:

```ts
export interface CardColumns {
  left: string[]
  right: string[]
}

export const CARD_COLUMNS_STORAGE_KEY = 'library:doc-layout-card-columns-v1'

export const DEFAULT_CARD_COLUMNS: CardColumns = {
  left: ['notes', 'metadata', 'comments', 'actions', 'history'],
  right: ['preview', 'markdown', 'series-chart'],
}

/** Old flat -> two columns, splitting by the historical column membership. */
const LEGACY_RIGHT = new Set(['preview', 'markdown', 'series-chart'])
export function migrateCardOrderToColumns(flatOrder: readonly string[]): CardColumns {
  const left: string[] = []
  const right: string[] = []
  for (const id of flatOrder) (LEGACY_RIGHT.has(id) ? right : left).push(id)
  return reconcileCardColumns({ left, right }, DEFAULT_CARD_COLUMNS)
}

/** Keep the user's order/placement for still-valid cards; append missing known
 * cards to their default column; drop unknown; ensure each card appears once. */
export function reconcileCardColumns(
  stored: Partial<CardColumns> | null | undefined,
  defaults: CardColumns,
): CardColumns {
  const known = new Set([...defaults.left, ...defaults.right])
  const seen = new Set<string>()
  const take = (ids: readonly string[] | undefined): string[] => {
    const out: string[] = []
    for (const id of ids ?? []) {
      if (known.has(id) && !seen.has(id)) {
        seen.add(id)
        out.push(id)
      }
    }
    return out
  }
  const left = take(stored?.left)
  const right = take(stored?.right)
  // Append any known-but-unplaced card to its default column.
  for (const id of defaults.left) if (!seen.has(id)) { seen.add(id); left.push(id) }
  for (const id of defaults.right) if (!seen.has(id)) { seen.add(id); right.push(id) }
  return { left, right }
}
```

Replace the flat `cardOrder` storage/init with the column model, migrating the old key on first read:

```ts
const cardColumns = useStorage<CardColumns>(CARD_COLUMNS_STORAGE_KEY, { ...DEFAULT_CARD_COLUMNS })
// One-time migration: if the new key was empty (defaults) but an old flat order exists, split it.
{
  const legacy = localStorage.getItem(CARD_ORDER_STORAGE_KEY)
  const isFresh = !localStorage.getItem(CARD_COLUMNS_STORAGE_KEY)
  if (isFresh && legacy) {
    try {
      const flat = JSON.parse(legacy) as unknown
      if (Array.isArray(flat)) cardColumns.value = migrateCardOrderToColumns(flat as string[])
    } catch {
      /* ignore malformed legacy value */
    }
  }
}
cardColumns.value = reconcileCardColumns(cardColumns.value, DEFAULT_CARD_COLUMNS)
```

Mutators + reset:

```ts
function moveCard(cardId: string, toColumn: 'left' | 'right', toIndex: number): void {
  const next: CardColumns = { left: [...cardColumns.value.left], right: [...cardColumns.value.right] }
  next.left = next.left.filter((id) => id !== cardId)
  next.right = next.right.filter((id) => id !== cardId)
  const dest = next[toColumn]
  const clamped = Math.max(0, Math.min(toIndex, dest.length))
  dest.splice(clamped, 0, cardId)
  cardColumns.value = next
}

function setColumn(column: 'left' | 'right', ids: readonly string[]): void {
  cardColumns.value = { ...cardColumns.value, [column]: [...ids] }
}
```

In `resetLayout()` set `cardColumns.value = { ...DEFAULT_CARD_COLUMNS, left: [...DEFAULT_CARD_COLUMNS.left], right: [...DEFAULT_CARD_COLUMNS.right] }` (keep the existing hero-field reset). Remove `cardOrder`, `setCardOrder`, `reconcileCardOrder`, `DEFAULT_CARD_ORDER` and the old index-based `moveCard`; keep `CARD_ORDER_STORAGE_KEY` as an exported const **only** for the migration read (or inline the string). Update the `DocumentLayout` interface + the returned object accordingly.

- [ ] **Step 4: Run tests**

Run: `cd frontend && npx vitest run src/composables/__tests__/useDocumentLayout.spec.ts`
Expected: PASS. (Type-check will flag `DocumentDetailView.vue`'s now-removed `cardOrder` usage — that is Part 2 of this task, below. Do NOT commit yet; complete Part 2 first so the whole task lands green.)

- [ ] **Step 5: Commit Part 1**

```bash
git add frontend/src/composables/useDocumentLayout.ts frontend/src/composables/__tests__/useDocumentLayout.spec.ts
git commit -m "feat(layout): two-column card model + legacy migration in useDocumentLayout"
```

### Part 2 — Free-form cross-column drag in `DocumentDetailView`

**Files:**
- Modify: `frontend/src/views/DocumentDetailView.vue`
- Test: `frontend/src/views/__tests__/DocumentDetailView.spec.ts`

**Interfaces:**
- Consumes: `cardColumns`, `moveCard(cardId, toColumn, toIndex)` (Task 1).

- [ ] **Step 1: Write failing test**

Extend the DocumentDetailView spec (mock `sortablejs` as the file already does; reset the layout singleton in `beforeEach`). Assert that invoking the drag `onEnd` for a left→right move updates `cardColumns` and re-renders the card in the right column:

```ts
it('moves a card across columns via the shared-group onEnd handler', async () => {
  const layout = useDocumentLayout()
  layout.resetLayout()
  const w = await mountView()
  await w.find('[data-testid="edit-layout-toggle"]').trigger('click')
  await flushPromises()
  // Simulate SortableJS dropping "comments" (left index 2) into the right column at index 0.
  // The component exposes the handler through the captured Sortable options (see mock);
  // call the metadata/preview onEnd with a stub event.
  const evt = {
    from: { dataset: { col: 'left' }, insertBefore: vi.fn(), children: [] },
    to: { dataset: { col: 'right' } },
    item: {}, oldIndex: 2, newIndex: 0,
  }
  capturedOnEnd(evt) // however the spec captures the Sortable.create options.onEnd
  await flushPromises()
  expect(layout.cardColumns.value.right[0]).toBe('comments')
  expect(layout.cardColumns.value.left).not.toContain('comments')
})
```

(Adapt `capturedOnEnd` to the spec's existing Sortable mock — the current spec already captures `Sortable.create` options to drive reorder tests; reuse that capture.)

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run src/views/__tests__/DocumentDetailView.spec.ts`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `DocumentDetailView.vue`:
- Remove `PREVIEW_CARD_IDS`/`METADATA_CARD_IDS`, `previewCards`/`metadataCards` (derived from the old flat `cardOrder`), and `reorderCards`.
- Render columns from the composable: `const { cardColumns, moveCard } = useDocumentLayout()`. `previewCards = computed(() => cardColumns.value.right.filter(cardPresent))`, `metadataCards = computed(() => cardColumns.value.left.filter(cardPresent))`. Keep the `v-for="cardId in previewCards"` / `metadataCards` loops and the `section-card-{id}` wrappers.
- Add `data-col="right"` to the preview column container (`previewColumnEl`) and `data-col="left"` to the metadata column container (`metadataColumnEl`).
- Give both column `Sortable.create` calls `group: 'doc-cards'` and a shared `onEnd`:

```ts
function onCardDragEnd(evt: Sortable.SortableEvent): void {
  const fromCol = (evt.from as HTMLElement).dataset.col as 'left' | 'right'
  const toCol = (evt.to as HTMLElement).dataset.col as 'left' | 'right'
  if (evt.oldIndex == null || evt.newIndex == null) return
  // The rendered lists are filtered by cardPresent; map DOM index -> card id via the
  // present list for the source column so the id is correct even with hidden cards.
  const sourceList = fromCol === 'left' ? metadataCards.value : previewCards.value
  const cardId = sourceList[evt.oldIndex]
  if (!cardId) return
  // Revert SortableJS's DOM mutation so Vue re-renders from the reactive arrays
  // (prevents the cross-list duplicate node).
  const ref = evt.from.children[evt.oldIndex] ?? null
  evt.from.insertBefore(evt.item, ref)
  moveCard(cardId, toCol, evt.newIndex)
}
```

Wire `onCardDragEnd` as the `onEnd` for BOTH column Sortables (replace the two separate `reorderCards` handlers).

Note on index mapping: `moveCard`'s `toIndex` is an index into the destination column's FULL list (present + hidden). Because hidden cards render no DOM, `evt.newIndex` is an index into the *present* destination list; convert it to a full-list index by counting present cards. Simplest correct approach: compute the destination card id that `newIndex` lands before among present cards, then find that id's index in the full `cardColumns[toCol]`; if `newIndex` is at the end of the present list, append. Implement a small helper `presentIndexToFullIndex(fullList, presentPredicate, presentIndex)` and unit-cover it if the doc has hidden cards; for the common all-present case they are equal.

- [ ] **Step 4: Run tests**

Run: `cd frontend && npx vitest run src/views/__tests__/DocumentDetailView.spec.ts && npm run type-check && npx eslint src/views/DocumentDetailView.vue`
Expected: PASS, exit 0.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/DocumentDetailView.vue frontend/src/views/__tests__/DocumentDetailView.spec.ts
git commit -m "feat(layout): free-form cross-column tile drag (shared group + DOM-revert)"
```

---

## Task 2 (B1): Backend `dock_position` appearance preference

**Files:**
- Modify: `src/library/schemas.py` (near `BackgroundTone`/`AppearancePreferences`, ~:333-405)
- Modify: `src/library/api/settings.py` (`put_appearance`, ~:54-66)
- Test: `tests/test_api_settings.py` (or the existing settings test module — locate it)

**Interfaces:**
- Produces: `DockPosition` StrEnum; `dock_position` on `AppearancePreferences` (default `top-right`, unknown→default before-validator); the resolved-prefs blob includes `dock_position`; `PUT /api/settings/appearance` persists it.

- [ ] **Step 1: Write failing test**

In the settings API test module (mirror the existing appearance test — find how it PUTs `/api/settings/appearance`):

```python
def test_appearance_persists_dock_position_and_defaults(client, seed_user):
    # unknown/missing resolves to top-right
    r = client.get("/api/settings")
    assert r.json()["dock_position"] == "top-right"
    # round-trip a valid value
    r = client.put("/api/settings/appearance",
                   json={"background_tone": "neutral", "tile_preview": "facsimile", "dock_position": "bottom-left"})
    assert r.status_code == 200
    assert r.json()["dock_position"] == "bottom-left"
    # invalid value is rejected OR coerced to default per the before-validator pattern — assert the file's chosen behavior
```

(Match the existing appearance test's fixtures + the exact `tile_preview` enum value it uses.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_api_settings.py -v -k appearance`
Expected: FAIL (`dock_position` absent).

- [ ] **Step 3: Implement**

In `schemas.py`, mirror `BackgroundTone`/`_resolve_background_tone`:

```python
class DockPosition(StrEnum):
    TOP_LEFT = "top-left"
    TOP_MIDDLE = "top-middle"
    TOP_RIGHT = "top-right"
    BOTTOM_LEFT = "bottom-left"
    BOTTOM_RIGHT = "bottom-right"


DEFAULT_DOCK_POSITION: Final[DockPosition] = DockPosition.TOP_RIGHT


def _resolve_dock_position(blob: dict[str, Any]) -> DockPosition:
    raw = blob.get("dock_position")
    if isinstance(raw, str) and raw in {p.value for p in DockPosition}:
        return DockPosition(raw)
    return DEFAULT_DOCK_POSITION
```

Add to `AppearancePreferences` a `dock_position: DockPosition = DEFAULT_DOCK_POSITION` field with the same `@field_validator(..., mode="before")` unknown→default pattern as `background_tone`. Add `dock_position` to whatever model `resolve_preferences` returns (the `UserPreferences` resolved shape), populated via `_resolve_dock_position(blob)` — mirror exactly how `background_tone` is resolved there.

In `settings.py` `put_appearance`, add to the merged dict: `"dock_position": payload.dock_position.value` (alongside `background_tone`/`tile_preview`).

- [ ] **Step 4: Run tests + lint**

Run: `uv run pytest tests/test_api_settings.py -v -k appearance && uv run ruff format --check src/library/schemas.py src/library/api/settings.py && uv run ruff check src/library/schemas.py src/library/api/settings.py`
Expected: PASS, clean.

- [ ] **Step 5: Commit**

```bash
git add src/library/schemas.py src/library/api/settings.py tests/test_api_settings.py
git commit -m "feat(settings): dock_position appearance preference (JSONB, no migration)"
```

---

## Task 3 (B2): Frontend preference plumbing

**Files:**
- Modify: `frontend/src/api/settings.ts` (~:155-185)
- Modify: `frontend/src/stores/auth.ts` (~:46-120)
- Test: `frontend/src/stores/__tests__/auth.spec.ts` (or wherever the store is tested)

**Interfaces:**
- Produces: `DockPosition` TS type + `DEFAULT_DOCK_POSITION = 'top-right'`; `dock_position?: DockPosition` on `UserPreferences`; `updateAppearance(tone, tilePreview, dockPosition)` sends `dock_position`; `auth.dockPosition` computed.
- Consumes: backend `dock_position` (Task 2).

- [ ] **Step 1: Write failing store test**

```ts
it('exposes dockPosition from preferences, defaulting to top-right', () => {
  const auth = useAuthStore()
  auth.applyPreferences({ /* minimal prefs without dock_position */ } as any)
  expect(auth.dockPosition).toBe('top-right')
  auth.applyPreferences({ /* ...prefs */, dock_position: 'bottom-left' } as any)
  expect(auth.dockPosition).toBe('bottom-left')
})
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run src/stores/__tests__/auth.spec.ts`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `settings.ts`: add
```ts
export const DOCK_POSITIONS = ['top-left', 'top-middle', 'top-right', 'bottom-left', 'bottom-right'] as const
export type DockPosition = (typeof DOCK_POSITIONS)[number]
export const DEFAULT_DOCK_POSITION: DockPosition = 'top-right'
```
Add `dock_position?: DockPosition` to `UserPreferences`. Extend `updateAppearance` to a 3rd param `dockPosition: DockPosition` and include `dock_position: dockPosition` in the PUT body. Update its two existing call sites in `SettingsView.vue` to pass the current dock position (from `auth.dockPosition`) — done fully in Task 6, but update the signature + body here.

In `auth.ts`: mirror `backgroundTone`:
```ts
const dockPosition = computed<DockPosition>(
  () => user.value?.preferences?.dock_position ?? DEFAULT_DOCK_POSITION,
)
```
Export `dockPosition` in the store's return.

- [ ] **Step 4: Run tests + type-check**

Run: `cd frontend && npx vitest run src/stores/__tests__/auth.spec.ts && npm run type-check`
Expected: PASS. (SettingsView call sites of `updateAppearance` now need the 3rd arg — fix them minimally here by passing `auth.dockPosition`, full control lands in Task 6.)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/settings.ts frontend/src/stores/auth.ts frontend/src/stores/__tests__/auth.spec.ts frontend/src/views/SettingsView.vue
git commit -m "feat(settings): dockPosition store computed + updateAppearance param"
```

---

## Task 4 (B3): Extract `ActionDock.vue` (rename + positioning)

**Files:**
- Create: `frontend/src/components/ActionDock.vue`
- Modify: `frontend/src/views/DocumentDetailView.vue` (replace inline island with `<ActionDock>`)
- Test: `frontend/src/components/__tests__/ActionDock.spec.ts`

**Interfaces:**
- Consumes: `auth.dockPosition` (Task 3), `useMetadataEditMode`, the shared `askHref` computed.
- Produces: `<ActionDock :ask-href="askHref" />` rendering testids `action-dock`, `action-dock-ask`, `action-dock-edit-toggle`.

- [ ] **Step 1: Write failing test**

`ActionDock.spec.ts` (mock the auth store to control `dockPosition`):

```ts
it.each([
  ['top-left', 'top-0', 'justify-start'],
  ['top-middle', 'top-0', 'justify-center'],
  ['top-right', 'top-0', 'justify-end'],
  ['bottom-left', 'bottom-0', 'justify-start'],
  ['bottom-right', 'bottom-0', 'justify-end'],
])('positions the dock wrapper for %s', (pos, edgeClass, justifyClass) => {
  const w = mountDock(pos as DockPosition)
  const wrapper = w.find('[data-testid="action-dock-wrapper"]')
  expect(wrapper.classes()).toContain(edgeClass)
  expect(wrapper.classes()).toContain(justifyClass)
})

it('renders the Ask anchor and toggles metadata edit mode', async () => {
  const w = mountDock('top-right')
  expect(w.find('[data-testid="action-dock-ask"]').attributes('href')).toBeTruthy()
  await w.find('[data-testid="action-dock-edit-toggle"]').trigger('click')
  expect(useMetadataEditMode().editMode.value).toBe(true)
})
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run src/components/__tests__/ActionDock.spec.ts`
Expected: FAIL (component absent).

- [ ] **Step 3: Implement `ActionDock.vue`**

Move the existing island markup (the pill with the Ask anchor + Edit/Done toggle) out of `DocumentDetailView.vue` into `ActionDock.vue`. Props: `askHref: string`. Consume `useMetadataEditMode` for the toggle. Structure:

```html
<template>
  <div
    data-testid="action-dock-wrapper"
    class="pointer-events-none sticky z-40 flex px-4"
    :class="[edgeClass, justifyClass]"
  >
    <div data-testid="action-dock" class="pointer-events-auto flex items-center gap-2 rounded-full border ...">
      <AppButton :href="askHref" target="_blank" data-testid="action-dock-ask" ...>Ask</AppButton>
      <button data-testid="action-dock-edit-toggle" :aria-pressed="editMode" @click="toggle">…Edit/Done…</button>
    </div>
  </div>
</template>
```

```ts
const { editMode, toggle } = useMetadataEditMode()
const dockPosition = computed(() => useAuthStore().dockPosition)
const edgeClass = computed(() => (dockPosition.value.startsWith('top') ? 'top-0 self-start' : 'bottom-0 self-end'))
const justifyClass = computed(() =>
  dockPosition.value.endsWith('left') ? 'justify-start'
  : dockPosition.value.endsWith('right') ? 'justify-end'
  : 'justify-center',
)
```

Keep the pill's existing classes/icons from the old island. The `sticky` wrapper is content-width (it lives in the detail page inside `#app-content`), `pointer-events-none`, with the dock `pointer-events-auto`. For top positions add a top offset that clears the header (use the same value the app uses for the sticky header, e.g. a Tailwind `top-16`/`top-[var(--header-h)]` — check `AppHeader`/`DefaultLayout` for the header height; if the header is not sticky, `top-0` is correct since the dock only appears after the hero scrolls off). Report the exact offset you used.

In `DocumentDetailView.vue`: replace the inline island block with `<ActionDock v-if="!heroVisible" :ask-href="askHref" />` (keep the IntersectionObserver + `heroVisible`). Remove the old `detail-island`/`island-*` markup. Update any DocumentDetailView spec assertions that referenced the old island testids to the new ones (or to asserting `<ActionDock>` presence).

- [ ] **Step 4: Run tests + type-check + lint + build**

Run: `cd frontend && npx vitest run src/components/__tests__/ActionDock.spec.ts src/views/__tests__/DocumentDetailView.spec.ts && npm run type-check && npx eslint src/components/ActionDock.vue src/views/DocumentDetailView.vue && npm run build-only`
Expected: PASS, exit 0.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ActionDock.vue frontend/src/components/__tests__/ActionDock.spec.ts frontend/src/views/DocumentDetailView.vue frontend/src/views/__tests__/DocumentDetailView.spec.ts
git commit -m "feat(detail): extract ActionDock with configurable position"
```

---

## Task 5 (B4): Appearance-tab "Action dock position" control

**Files:**
- Modify: `frontend/src/views/SettingsView.vue`
- Test: `frontend/src/views/__tests__/SettingsView.spec.ts`

**Interfaces:**
- Consumes: `updateAppearance` (Task 3), `auth.dockPosition`, `DOCK_POSITIONS`.

- [ ] **Step 1: Write failing test**

```ts
it('saves the chosen dock position optimistically', async () => {
  const w = mountSettings() // open the appearance tab as the spec's existing tests do
  await w.find('[data-testid="settings-tab-appearance"]').trigger('click')
  await w.find('[data-testid="dock-position-bottom-left"]').trigger('click')
  await flushPromises()
  expect(updateAppearanceMock).toHaveBeenCalledWith('neutral', expect.anything(), 'bottom-left')
  expect(useAuthStore().dockPosition).toBe('bottom-left') // optimistic store update
})
```

(Mock `updateAppearance` as the appearance tests already do; match the current tone/tile args.)

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run src/views/__tests__/SettingsView.spec.ts`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `SettingsView.vue` Appearance tab, add an "Action dock position" section: a labelled group of 5 buttons over `DOCK_POSITIONS` (testids `dock-position-{value}`, container `settings-dock-position`, uppercase-xs label, active state on the current value). On click, mirror the tone-swatch handler exactly: optimistic `auth.applyPreferences({ ...auth.user.preferences, dock_position: value })`, then `await updateAppearance(selectedTone.value, selectedTilePreview.value, value)`, with the same error-toast rollback pattern (`toneError`) on failure. Also update the existing tone and tile handlers' `updateAppearance(...)` calls to pass `auth.dockPosition` as the 3rd arg (so they don't reset the dock).

- [ ] **Step 4: Run tests + type-check + lint**

Run: `cd frontend && npx vitest run src/views/__tests__/SettingsView.spec.ts && npm run type-check && npx eslint src/views/SettingsView.vue`
Expected: PASS, exit 0.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/SettingsView.vue frontend/src/views/__tests__/SettingsView.spec.ts
git commit -m "feat(settings): Action dock position control in the Appearance tab"
```

---

## Task 6: Documentation

**Files:**
- Modify: `docs/frontend.md`, `docs/api.md`

- [ ] **Step 1: Update `docs/frontend.md`** — the detail view's tile reorder is now free-form cross-column (two-column persisted model, migrated from the old flat order); rename the "island" to the **Action dock**, note its 5 positions + Appearance setting + appear-on-scroll trigger; add the "Action dock position" control to the Appearance-tab description. Verify claims against the code; keep tables well-formed; H1 clean + decimal subheadings.
- [ ] **Step 2: Update `docs/api.md`** — `PUT /api/settings/appearance` and `GET /api/settings` now include `dock_position` (enum + default `top-right`).
- [ ] **Step 3: Commit**

```bash
git add docs/
git commit -m "docs: free-form tile reorder + Action dock position setting"
```

---

## Final verification (before merge)

- [ ] Frontend: `cd frontend && npm run test:unit -- run` (green) + `npm run type-check` + `npm run lint` + `npm run build-only`.
- [ ] Backend: `uv run pytest` (green) + `uv run ruff format --check .` + `uv run ruff check .`.
- [ ] Manual smoke: in Edit layout mode, drag `comments` from the left column to the right above `markdown` and reload (persists). Change the Action dock position in Settings → Appearance and confirm it moves (below navbar, within content, not over the sidebar) for all 5 values, appearing once the hero scrolls off.

## Self-review against the spec

- Spec §3.1 (two-column model + migration + reconcile) → Task 1 Part 1. §3.2 (shared-group cross-column drag + DOM revert) → Task 1 Part 2. §3.3 (responsive/present cards) → Task 1 Part 2 (index mapping). §4.1 (rename + extraction + appear-on-scroll) → Task 4. §4.2 (backend dock_position, no migration) → Task 2. §4.3 (frontend prefs plumbing) → Task 3. §4.4 (positioning) → Task 4. §4.5 (tests) → distributed. Docs (§5.7) → Task 6. No spec section unmapped.
- Type consistency: `CardColumns`/`moveCard(cardId,toColumn,toIndex)` (Task 1) consumed within Task 1 Part 2; `DockPosition` enum values identical backend (Task 2) + frontend (Task 3); `updateAppearance(tone, tile, dockPosition)` (Task 3) consumed by Tasks 4-5; `auth.dockPosition` (Task 3) consumed by Tasks 4-5; testids `action-dock*` (Task 4) consumed by Task 5 / the DocumentDetailView spec.
