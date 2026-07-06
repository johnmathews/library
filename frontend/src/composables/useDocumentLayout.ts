import { ref, type Ref } from 'vue'
import { useStorage } from '@vueuse/core'

/**
 * Shared, per-user layout state for the document-detail page.
 *
 * The detail page lets each user tailor how a document is presented: which
 * metadata fields show in the hero (and in what order), and the vertical order
 * of the page's cards. Those two preferences persist per-machine so the page
 * comes back the way the user left it. A third piece — `editMode` — is the
 * ephemeral "am I currently rearranging the layout" flag; it is intentionally
 * NOT persisted so a reload always returns to the normal (non-editing) view.
 *
 * This is singleton state (module-level refs). Later units render the hero-field
 * customiser and the card reorderer from *separate* components, but they must
 * share one `editMode` (entering edit mode reveals both affordances at once), so
 * every `useDocumentLayout()` caller gets the same underlying refs.
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
 * current position/visibility. `created_at`/`updated_at` read as "Ingested" /
 * "Last edited" rather than their raw column names.
 */
export const HERO_FIELD_LABELS: Record<string, string> = {
  kind: 'Kind',
  sender: 'Sender',
  recipient: 'Recipient',
  document_date: 'Document date',
  created_at: 'Ingested',
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

/** Stable card ids, split into their default column and default in-column order. */
export const DEFAULT_CARD_COLUMNS: CardColumns = {
  left: ['notes', 'metadata', 'comments', 'actions', 'history'],
  right: ['preview', 'markdown', 'series-chart'],
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
 *  - append known keys missing from storage at their default position/visibility.
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
const LEGACY_RIGHT = new Set(['preview', 'markdown', 'series-chart'])

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
cardColumns.value = reconcileCardColumns(cardColumns.value, DEFAULT_CARD_COLUMNS)

// Ephemeral — deliberately a plain ref, never persisted.
const editMode = ref(false)

function toggleEditMode(): void {
  editMode.value = !editMode.value
}

function setEditMode(value: boolean): void {
  editMode.value = value
}

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
  /** Ephemeral "editing the layout" flag (not persisted; resets on reload). */
  editMode: Ref<boolean>
  toggleEditMode: () => void
  setEditMode: (value: boolean) => void
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
    editMode,
    toggleEditMode,
    setEditMode,
    setHeroFieldVisible,
    moveHeroField,
    setHeroFieldOrder,
    setColumn,
    moveCard,
    resetLayout,
  }
}
