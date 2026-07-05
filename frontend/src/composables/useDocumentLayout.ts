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
 * DEFAULT_* constants on read (see `reconcileHeroFields` / `reconcileCardOrder`)
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
/** localStorage key for the persisted card order. */
export const CARD_ORDER_STORAGE_KEY = 'library:doc-layout-card-order-v1'

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

/** Stable card ids in their current visual order. */
export const DEFAULT_CARD_ORDER: readonly string[] = [
  'preview',
  'markdown',
  'series-chart',
  'notes',
  'metadata',
  'comments',
  'actions',
  'history',
]

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
 * Reconcile a stored card order against the current defaults, with the same
 * keep/drop/append semantics as `reconcileHeroFields`.
 */
export function reconcileCardOrder(
  stored: readonly string[] | null | undefined,
  defaults: readonly string[],
): string[] {
  const defaultSet = new Set(defaults)
  const storedList = Array.isArray(stored) ? stored : []
  const seen = new Set<string>()
  const result: string[] = []
  for (const id of storedList) {
    if (defaultSet.has(id) && !seen.has(id)) {
      result.push(id)
      seen.add(id)
    }
  }
  for (const id of defaults) {
    if (!seen.has(id)) {
      result.push(id)
      seen.add(id)
    }
  }
  return result
}

// --- Singleton state (module-level, shared across every caller) --------------

const heroFields = useStorage<HeroField[]>(
  HERO_FIELDS_STORAGE_KEY,
  cloneHeroFields(DEFAULT_HERO_FIELDS),
)
// Reconcile whatever was persisted against the current defaults on first load.
heroFields.value = reconcileHeroFields(heroFields.value, DEFAULT_HERO_FIELDS)

const cardOrder = useStorage<string[]>(CARD_ORDER_STORAGE_KEY, [...DEFAULT_CARD_ORDER])
cardOrder.value = reconcileCardOrder(cardOrder.value, DEFAULT_CARD_ORDER)

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
 * Set the card order to `ids`. Unknown ids are ignored and any current card
 * omitted from `ids` is appended, so a partial list can never drop a card.
 */
function setCardOrder(ids: readonly string[]): void {
  const current = new Set(cardOrder.value)
  const seen = new Set<string>()
  const result: string[] = []
  for (const id of ids) {
    if (current.has(id) && !seen.has(id)) {
      result.push(id)
      seen.add(id)
    }
  }
  for (const id of cardOrder.value) {
    if (!seen.has(id)) {
      result.push(id)
      seen.add(id)
    }
  }
  cardOrder.value = result
}

/** Move a card from one index to another. */
function moveCard(fromIndex: number, toIndex: number): void {
  cardOrder.value = moveItem(cardOrder.value, fromIndex, toIndex)
}

/** Restore both persisted preferences to their DEFAULT_* values. */
function resetLayout(): void {
  heroFields.value = cloneHeroFields(DEFAULT_HERO_FIELDS)
  cardOrder.value = [...DEFAULT_CARD_ORDER]
}

export interface DocumentLayout {
  /** Ordered, persisted hero fields (key + visibility). */
  heroFields: Ref<HeroField[]>
  /** Ordered, persisted card ids. */
  cardOrder: Ref<string[]>
  /** Ephemeral "editing the layout" flag (not persisted; resets on reload). */
  editMode: Ref<boolean>
  toggleEditMode: () => void
  setEditMode: (value: boolean) => void
  setHeroFieldVisible: (key: string, visible: boolean) => void
  moveHeroField: (fromIndex: number, toIndex: number) => void
  setHeroFieldOrder: (keys: readonly string[]) => void
  setCardOrder: (ids: readonly string[]) => void
  moveCard: (fromIndex: number, toIndex: number) => void
  resetLayout: () => void
}

export function useDocumentLayout(): DocumentLayout {
  return {
    heroFields,
    cardOrder,
    editMode,
    toggleEditMode,
    setEditMode,
    setHeroFieldVisible,
    moveHeroField,
    setHeroFieldOrder,
    setCardOrder,
    moveCard,
    resetLayout,
  }
}
