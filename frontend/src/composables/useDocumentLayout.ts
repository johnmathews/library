import { type Ref } from 'vue'
import { useStorage } from '@vueuse/core'

/**
 * Shared, per-user layout state for the document-detail page.
 *
 * The detail page lets each user tailor how a document is presented: which
 * metadata fields show in the hero (and in what order), and the vertical order
 * of the page's cards. Both persist per-machine so the page comes back the way
 * the user left it.
 *
 * This module owns PERSISTED layout only. The ephemeral "am I editing" flag
 * lives in `useMetadataEditMode` and is deliberately not duplicated here: there
 * used to be two flags — "Edit details" (field values) and "Edit layout"
 * (arrangement) — behind two adjacent buttons, and pressing the wrong one was
 * the single most-reported annoyance on this page. One mode, one owner.
 *
 * This is singleton state (module-level refs), so every `useDocumentLayout()`
 * caller gets the same underlying refs.
 *
 * Merge-safe loading: stored preferences are reconciled against the current
 * DEFAULT_* constants on read (see `reconcileHeroFields` / `reconcileCardColumns`)
 * so a user with an older saved layout still gets newly-added fields/cards, and
 * keys we have since removed don't linger.
 */

/** One entry in the hero-field list: a known field key + whether it renders. */
export interface HeroField {
  key: string
  visible: boolean
}

/** localStorage key for the persisted hero-field list (order + visibility). */
export const HERO_FIELDS_STORAGE_KEY = 'library:doc-layout-hero-fields-v1'
/** localStorage key for the legacy (pre-column) flat card order. Kept only so
 * `useDocumentLayout`'s init can migrate a user's existing value once — no
 * longer read or written by anything else. */
export const CARD_ORDER_STORAGE_KEY = 'library:doc-layout-card-order-v1'
/** localStorage key for the persisted two-column card layout. */
export const CARD_COLUMNS_STORAGE_KEY = 'library:doc-layout-card-columns-v1'

/**
 * Human labels for every hero-eligible field key. Kept separate from the
 * ordered list so a component can render a label for a key regardless of its
 * current position/visibility. The two dates are named to disambiguate them:
 * `document_date` is the date printed on the document ("Date on document"),
 * `created_at` is when it entered the library ("Date added to library").
 * `updated_at` reads as "Last edited". These match the dashboard sort control
 * and the tile field picker.
 */
export const HERO_FIELD_LABELS: Record<string, string> = {
  kind: 'Kind',
  sender: 'Sender',
  recipient: 'Recipient',
  document_date: 'Date on document',
  created_at: 'Date added to library',
  updated_at: 'Last edited',
  amount: 'Amount',
  language: 'Language',
  due_date: 'Due date',
  expiry_date: 'Expiry date',
}

/**
 * Default hero fields in default order. The first block is visible out of the
 * box (recipient is visible by product requirement); the tail is hidden by
 * default but one toggle away.
 */
export const DEFAULT_HERO_FIELDS: readonly HeroField[] = [
  { key: 'kind', visible: true },
  { key: 'sender', visible: true },
  { key: 'recipient', visible: true },
  { key: 'document_date', visible: true },
  { key: 'created_at', visible: true },
  { key: 'updated_at', visible: true },
  { key: 'amount', visible: true },
  { key: 'language', visible: false },
  { key: 'due_date', visible: false },
  { key: 'expiry_date', visible: false },
]

/** Two named drop zones a card can live in; the page renders `left` beside
 * `right` on desktop and stacked (left above right) below `lg`. */
export interface CardColumns {
  left: string[]
  right: string[]
}

/**
 * The one metadata panel: kind/language/tags/projects/matters, sender,
 * recipient and dates, facet labels, and read-only provenance, in a single
 * card.
 *
 * The id is deliberately the string the ORIGINAL pre-split Details card used.
 * Between 2026-07-09 and 2026-09-06 it was split into four `metadata-*` tiles
 * plus a separate `facets` card; reusing `'metadata'` means an ancient layout
 * from before that split needs no migration of its own, which is why the
 * expansion migration could be deleted rather than kept alongside its inverse.
 */
export const METADATA_CARD_ID = 'metadata'

/** Card ids the metadata panel absorbed, collapsed into `METADATA_CARD_ID` by
 * `collapseMetadataCards` on load. `metadata-classification` is absent on
 * purpose: it was retired earlier and no longer appears in any live layout, so
 * `reconcileCardColumns` dropping it as unknown is the correct handling. */
export const RETIRED_METADATA_CARD_IDS = [
  'metadata-content',
  'metadata-parties',
  'metadata-financial',
  'metadata-system',
  'facets',
] as const

/**
 * Stable card ids, split into their default column and default in-column order.
 *
 * `metadata` sits second in `left`, directly under `notes` — the position the
 * Content tile held, since that is where a returning user's collapsed panel
 * anchors (see `collapseMetadataCards`). The `series-chart` card was a third
 * right-column entry until 2026-08-31; a layout still naming it is not broken,
 * because `reconcileCardColumns` drops any stored id absent from this constant
 * while preserving the order of the survivors.
 */
export const DEFAULT_CARD_COLUMNS: CardColumns = {
  left: ['notes', METADATA_CARD_ID, 'comments', 'actions', 'history'],
  right: ['preview', 'markdown'],
}

/** Fresh, mutable copy of a hero-field list (never share the constant's refs). */
function cloneHeroFields(fields: readonly HeroField[]): HeroField[] {
  return fields.map((f) => ({ key: f.key, visible: f.visible }))
}

/** Move an item within a list, returning a new array. Out-of-range is a no-op. */
function moveItem<T>(list: readonly T[], from: number, to: number): T[] {
  const result = list.slice()
  if (from < 0 || from >= result.length || to < 0 || to >= result.length) return result
  const [item] = result.splice(from, 1)
  result.splice(to, 0, item as T)
  return result
}

/**
 * Reconcile a stored hero-field list against the current defaults:
 *  - keep still-valid stored entries in their saved order + visibility;
 *  - drop stored keys no longer present in the defaults;
 *  - append known keys missing from storage at the END of the list, with
 *    their default visibility. NOT at their index in `defaults` — a key added
 *    to `DEFAULT_HERO_FIELDS` therefore lands last for every existing user,
 *    however high it is listed there. A key that must appear in a particular
 *    place needs a migration, the hero's analogue of `collapseMetadataCards`.
 * Pure — used both at init and directly in tests.
 */
export function reconcileHeroFields(
  stored: readonly HeroField[] | null | undefined,
  defaults: readonly HeroField[],
): HeroField[] {
  const defaultKeys = new Set(defaults.map((f) => f.key))
  const storedList = Array.isArray(stored) ? stored : []
  const seen = new Set<string>()
  const result: HeroField[] = []
  for (const field of storedList) {
    if (field && defaultKeys.has(field.key) && !seen.has(field.key)) {
      result.push({ key: field.key, visible: Boolean(field.visible) })
      seen.add(field.key)
    }
  }
  for (const def of defaults) {
    if (!seen.has(def.key)) {
      result.push({ key: def.key, visible: def.visible })
      seen.add(def.key)
    }
  }
  return result
}

/**
 * Reconcile a stored two-column card layout against the current defaults:
 *  - keep the user's saved order/placement for still-valid cards;
 *  - drop stored ids no longer known;
 *  - de-dupe (a card can appear only once across both columns);
 *  - append any known card missing from storage to its default column.
 */
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
  for (const id of defaults.left) {
    if (!seen.has(id)) {
      seen.add(id)
      left.push(id)
    }
  }
  for (const id of defaults.right) {
    if (!seen.has(id)) {
      seen.add(id)
      right.push(id)
    }
  }
  return { left, right }
}

/** Cards that lived in the (now-removed) flat order's preview column — used
 * only to split a legacy flat order into the new two-column shape. */
const LEGACY_RIGHT = new Set(['preview', 'markdown'])

/**
 * Migrate an old flat card order (pre-column model) into the two-column
 * shape, preserving each card's relative order within its historical column.
 * Pure partition only — it does not drop unknown ids, de-dupe, or append
 * missing known cards; the caller (composable init, below) always runs the
 * result through `reconcileCardColumns` right after, which does all of that.
 */
export function migrateCardOrderToColumns(flatOrder: readonly string[]): CardColumns {
  const left: string[] = []
  const right: string[] = []
  for (const id of flatOrder) (LEGACY_RIGHT.has(id) ? right : left).push(id)
  return { left, right }
}

/**
 * Collapse a stored two-column layout's per-section metadata tiles and its
 * `facets` card into the single `METADATA_CARD_ID` panel, in place.
 *
 * The FIRST retired id encountered — columns walked left then right — becomes
 * the panel and keeps that slot; every later one is dropped. Without this,
 * `reconcileCardColumns` would drop all five as unknown and append the panel at
 * the END of its default column, putting it below History for anyone who had
 * ever touched their layout.
 *
 * Two properties this must hold, both pinned in `useDocumentLayout.spec.ts`:
 * it runs on every page load, so it is **idempotent**; and because
 * `METADATA_CARD_ID` is itself the pre-2026-07-09 id, a layout predating the
 * split passes through unchanged rather than needing a second migration.
 *
 * Pure; a layout holding no retired id is returned structurally unchanged.
 */
export function collapseMetadataCards(cols: Partial<CardColumns> | null | undefined): CardColumns {
  const retired = new Set<string>(RETIRED_METADATA_CARD_IDS)
  let placed = false
  const collapse = (ids: readonly string[] | undefined): string[] => {
    const out: string[] = []
    for (const id of ids ?? []) {
      if (!retired.has(id) && id !== METADATA_CARD_ID) {
        out.push(id)
        continue
      }
      // The panel (or the first tile standing in for it) claims this slot.
      if (!placed) {
        out.push(METADATA_CARD_ID)
        placed = true
      }
    }
    return out
  }
  return { left: collapse(cols?.left), right: collapse(cols?.right) }
}

// --- Singleton state (module-level, shared across every caller) --------------

const heroFields = useStorage<HeroField[]>(
  HERO_FIELDS_STORAGE_KEY,
  cloneHeroFields(DEFAULT_HERO_FIELDS),
)
// Reconcile whatever was persisted against the current defaults on first load.
heroFields.value = reconcileHeroFields(heroFields.value, DEFAULT_HERO_FIELDS)

// Capture both localStorage reads BEFORE constructing the `useStorage` ref
// below: `useStorage` writes the serialized default synchronously on
// construction (writeDefaults defaults to true), so reading
// CARD_COLUMNS_STORAGE_KEY *after* construction would always find it
// present and this migration would never run.
const legacyOrder = localStorage.getItem(CARD_ORDER_STORAGE_KEY)
const hadColumns = localStorage.getItem(CARD_COLUMNS_STORAGE_KEY) !== null

const cardColumns = useStorage<CardColumns>(CARD_COLUMNS_STORAGE_KEY, { ...DEFAULT_CARD_COLUMNS })
// One-time migration: if the new key was empty (freshly defaulted) but an old
// flat order exists from before the column model, split it into columns so
// nothing visibly jumps for an existing user's saved order.
if (!hadColumns && legacyOrder) {
  try {
    const flat = JSON.parse(legacyOrder) as unknown
    if (Array.isArray(flat)) cardColumns.value = migrateCardOrderToColumns(flat as string[])
  } catch {
    /* ignore malformed legacy value */
  }
}
// Collapse the retired per-section metadata tiles and the old `facets` card
// into the single `metadata` panel, in place, BEFORE reconciliation — which
// would otherwise drop all five as unknown ids and append the panel at the
// column's end, below History.
cardColumns.value = collapseMetadataCards(cardColumns.value)
cardColumns.value = reconcileCardColumns(cardColumns.value, DEFAULT_CARD_COLUMNS)

/** Show or hide a hero field by key (no-op for an unknown key). */
function setHeroFieldVisible(key: string, visible: boolean): void {
  heroFields.value = heroFields.value.map((f) => (f.key === key ? { ...f, visible } : f))
}

/** Move a hero field from one index to another. */
function moveHeroField(fromIndex: number, toIndex: number): void {
  heroFields.value = moveItem(heroFields.value, fromIndex, toIndex)
}

/**
 * Reorder the hero fields to match `keys`. Unknown keys are ignored and any
 * current field omitted from `keys` is appended (preserving its visibility), so
 * a partial list can never silently drop a field.
 */
function setHeroFieldOrder(keys: readonly string[]): void {
  const byKey = new Map(heroFields.value.map((f) => [f.key, f]))
  const seen = new Set<string>()
  const result: HeroField[] = []
  for (const key of keys) {
    const field = byKey.get(key)
    if (field && !seen.has(key)) {
      result.push(field)
      seen.add(key)
    }
  }
  for (const field of heroFields.value) {
    if (!seen.has(field.key)) {
      result.push(field)
      seen.add(field.key)
    }
  }
  heroFields.value = result
}

/**
 * Set one column's card ids to `ids` (removing them from the other column
 * first, so a card can never appear in both). Unknown ids are simply
 * accepted as-is — reconciliation drops truly-unknown ids on the next read;
 * this setter is for direct column replacement (e.g. tests, bulk moves).
 */
function setColumn(column: 'left' | 'right', ids: readonly string[]): void {
  cardColumns.value = { ...cardColumns.value, [column]: [...ids] }
}

/** Move a card (by id) into `toColumn` at `toIndex`, removing it from
 * whichever column currently holds it first. Out-of-range `toIndex` clamps
 * to the destination column's bounds. */
function moveCard(cardId: string, toColumn: 'left' | 'right', toIndex: number): void {
  const next: CardColumns = {
    left: cardColumns.value.left.filter((id) => id !== cardId),
    right: cardColumns.value.right.filter((id) => id !== cardId),
  }
  const dest = next[toColumn]
  const clamped = Math.max(0, Math.min(toIndex, dest.length))
  dest.splice(clamped, 0, cardId)
  cardColumns.value = next
}

/** Restore both persisted preferences to their DEFAULT_* values. */
function resetLayout(): void {
  heroFields.value = cloneHeroFields(DEFAULT_HERO_FIELDS)
  cardColumns.value = {
    left: [...DEFAULT_CARD_COLUMNS.left],
    right: [...DEFAULT_CARD_COLUMNS.right],
  }
}

export interface DocumentLayout {
  /** Ordered, persisted hero fields (key + visibility). */
  heroFields: Ref<HeroField[]>
  /** Persisted two-column card layout (left/right, each an ordered id list). */
  cardColumns: Ref<CardColumns>
  setHeroFieldVisible: (key: string, visible: boolean) => void
  moveHeroField: (fromIndex: number, toIndex: number) => void
  setHeroFieldOrder: (keys: readonly string[]) => void
  setColumn: (column: 'left' | 'right', ids: readonly string[]) => void
  moveCard: (cardId: string, toColumn: 'left' | 'right', toIndex: number) => void
  resetLayout: () => void
}

export function useDocumentLayout(): DocumentLayout {
  return {
    heroFields,
    cardColumns,
    setHeroFieldVisible,
    moveHeroField,
    setHeroFieldOrder,
    setColumn,
    moveCard,
    resetLayout,
  }
}
