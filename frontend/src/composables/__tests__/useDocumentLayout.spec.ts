import { describe, it, expect, beforeEach, vi } from 'vitest'
import { nextTick } from 'vue'
import {
  useDocumentLayout,
  reconcileHeroFields,
  reconcileCardColumns,
  migrateCardOrderToColumns,
  DEFAULT_HERO_FIELDS,
  DEFAULT_CARD_COLUMNS,
  HERO_FIELD_LABELS,
  HERO_FIELDS_STORAGE_KEY,
  CARD_ORDER_STORAGE_KEY,
  CARD_COLUMNS_STORAGE_KEY,
  type HeroField,
  type CardColumns,
} from '../useDocumentLayout'

describe('reconcileHeroFields', () => {
  it('returns the defaults (order + visibility) when nothing is stored', () => {
    expect(reconcileHeroFields(null, DEFAULT_HERO_FIELDS)).toEqual(DEFAULT_HERO_FIELDS)
    expect(reconcileHeroFields([], DEFAULT_HERO_FIELDS)).toEqual(DEFAULT_HERO_FIELDS)
  })

  it('preserves the saved order and visibility of still-valid keys', () => {
    const defaults: HeroField[] = [
      { key: 'kind', visible: true },
      { key: 'sender', visible: true },
      { key: 'amount', visible: false },
    ]
    const stored: HeroField[] = [
      { key: 'amount', visible: true }, // user re-ordered + re-showed it
      { key: 'kind', visible: false }, // user hid it
      { key: 'sender', visible: true },
    ]
    expect(reconcileHeroFields(stored, defaults)).toEqual([
      { key: 'amount', visible: true },
      { key: 'kind', visible: false },
      { key: 'sender', visible: true },
    ])
  })

  it('appends a newly-added known key (missing from old stored state) with its default visibility', () => {
    const defaults: HeroField[] = [
      { key: 'kind', visible: true },
      { key: 'sender', visible: true },
      { key: 'language', visible: false }, // freshly added default
    ]
    const stored: HeroField[] = [
      { key: 'sender', visible: false },
      { key: 'kind', visible: true },
    ]
    expect(reconcileHeroFields(stored, defaults)).toEqual([
      { key: 'sender', visible: false },
      { key: 'kind', visible: true },
      { key: 'language', visible: false }, // appended at default position/visibility
    ])
  })

  it('drops a stored key that is no longer in the defaults', () => {
    const defaults: HeroField[] = [{ key: 'kind', visible: true }]
    const stored: HeroField[] = [
      { key: 'kind', visible: false },
      { key: 'legacy_field', visible: true }, // stale
    ]
    expect(reconcileHeroFields(stored, defaults)).toEqual([{ key: 'kind', visible: false }])
  })
})

describe('reconcileCardColumns', () => {
  it('returns the defaults when nothing is stored', () => {
    expect(reconcileCardColumns(null, DEFAULT_CARD_COLUMNS)).toEqual(DEFAULT_CARD_COLUMNS)
    expect(reconcileCardColumns({}, DEFAULT_CARD_COLUMNS)).toEqual(DEFAULT_CARD_COLUMNS)
  })

  it('appends missing known cards, drops unknown, de-dupes', () => {
    const stored = { left: ['metadata', 'metadata', 'ghost'], right: ['preview'] } // dup + unknown, missing several
    const merged = reconcileCardColumns(stored, DEFAULT_CARD_COLUMNS)
    const all = [...merged.left, ...merged.right]
    expect(all).not.toContain('ghost') // unknown dropped
    expect(all.filter((c) => c === 'metadata')).toHaveLength(1) // de-duped
    expect(new Set(all)).toEqual(
      new Set([...DEFAULT_CARD_COLUMNS.left, ...DEFAULT_CARD_COLUMNS.right]),
    ) // every known card present once
    expect(merged.left[0]).toBe('metadata') // preserved stored order for survivors
  })
})

describe('migrateCardOrderToColumns', () => {
  it('migrates an old flat card order into the two default columns preserving order', () => {
    const flat = ['history', 'markdown', 'metadata', 'preview'] // mixed columns, custom order
    const cols = migrateCardOrderToColumns(flat)
    expect(cols.left).toEqual(['history', 'metadata']) // metadata-column ids, in flat order
    expect(cols.right).toEqual(['markdown', 'preview']) // preview-column ids, in flat order
  })
})

describe('legacy card-order migration (module init)', () => {
  // These exercise the actual module-init migration block, not the pure
  // helpers above. `resetLayout()` (used by the other describe block's
  // `beforeEach`) never re-runs init, so seed localStorage *before* a fresh
  // module import via `vi.resetModules()`.
  beforeEach(() => {
    localStorage.clear()
    vi.resetModules()
  })

  it('migrates a customized legacy flat order into columns on first load', async () => {
    // Legacy user who moved markdown before preview/series-chart and kept a
    // custom left-column order, with no card-columns key yet.
    localStorage.setItem(
      CARD_ORDER_STORAGE_KEY,
      JSON.stringify([
        'history',
        'metadata',
        'markdown',
        'preview',
        'series-chart',
        'notes',
        'actions',
        'comments',
      ]),
    )
    const mod = await import('../useDocumentLayout')
    const layout = mod.useDocumentLayout()
    // Right column preserved the customized order (markdown before preview).
    expect(layout.cardColumns.value.right).toEqual(['markdown', 'preview', 'series-chart'])
    // Left column preserved the flat order for its members.
    expect(layout.cardColumns.value.left).toEqual(['history', 'metadata', 'notes', 'actions', 'comments'])
  })

  it('appends a known card missing from the legacy flat order to its default column', async () => {
    // Legacy user from before "comments" existed as a card.
    localStorage.setItem(
      CARD_ORDER_STORAGE_KEY,
      JSON.stringify(['history', 'metadata', 'notes', 'actions', 'markdown', 'preview', 'series-chart']),
    )
    const mod = await import('../useDocumentLayout')
    const layout = mod.useDocumentLayout()
    expect(layout.cardColumns.value.right).toEqual(['markdown', 'preview', 'series-chart'])
    // 'comments' wasn't in the legacy order; reconcileCardColumns appends it
    // to its default (left) column rather than dropping it.
    expect(layout.cardColumns.value.left).toEqual(['history', 'metadata', 'notes', 'actions', 'comments'])
  })

  it('a fresh user with no legacy key gets the default columns', async () => {
    const mod = await import('../useDocumentLayout')
    const layout = mod.useDocumentLayout()
    expect(layout.cardColumns.value).toEqual(mod.DEFAULT_CARD_COLUMNS)
  })

  it('does not migrate when a card-columns value is already persisted', async () => {
    // Simulate a returning user on the new key; the legacy key existing too
    // (e.g. never cleaned up) must not override their already-migrated state.
    localStorage.setItem(CARD_COLUMNS_STORAGE_KEY, JSON.stringify({ left: ['notes'], right: [] }))
    localStorage.setItem(CARD_ORDER_STORAGE_KEY, JSON.stringify(['history', 'markdown']))
    const mod = await import('../useDocumentLayout')
    const layout = mod.useDocumentLayout()
    expect(layout.cardColumns.value.left[0]).toBe('notes')
    expect(layout.cardColumns.value.right).not.toContain('history')
  })
})

describe('useDocumentLayout', () => {
  beforeEach(() => {
    localStorage.clear()
    useDocumentLayout().resetLayout()
    useDocumentLayout().setEditMode(false)
  })

  it('exposes hero-field defaults with recipient visible by default', () => {
    const { heroFields } = useDocumentLayout()
    expect(heroFields.value).toEqual(DEFAULT_HERO_FIELDS)
    const recipient = heroFields.value.find((f) => f.key === 'recipient')
    expect(recipient?.visible).toBe(true)
  })

  it('ships the expected default visible order and hidden tail', () => {
    const { heroFields } = useDocumentLayout()
    const visible = heroFields.value.filter((f) => f.visible).map((f) => f.key)
    const hidden = heroFields.value.filter((f) => !f.visible).map((f) => f.key)
    expect(visible).toEqual([
      'kind',
      'sender',
      'recipient',
      'document_date',
      'created_at',
      'updated_at',
      'amount',
    ])
    expect(hidden).toEqual(['language', 'due_date', 'expiry_date'])
  })

  it('exposes the default card columns', () => {
    const { cardColumns } = useDocumentLayout()
    expect(cardColumns.value).toEqual(DEFAULT_CARD_COLUMNS)
  })

  it('provides a human label for every hero field key', () => {
    for (const field of DEFAULT_HERO_FIELDS) {
      expect(HERO_FIELD_LABELS[field.key]).toBeTruthy()
    }
    expect(HERO_FIELD_LABELS.created_at).toBe('Added date')
    expect(HERO_FIELD_LABELS.updated_at).toBe('Last edited')
  })

  it('is a singleton: hero and card consumers share one editMode', () => {
    const a = useDocumentLayout()
    const b = useDocumentLayout()
    a.setEditMode(true)
    expect(b.editMode.value).toBe(true)
  })

  it('toggles editMode and does not persist it (ephemeral)', async () => {
    const { editMode, toggleEditMode, setEditMode } = useDocumentLayout()
    expect(editMode.value).toBe(false)
    toggleEditMode()
    expect(editMode.value).toBe(true)
    setEditMode(false)
    expect(editMode.value).toBe(false)
    toggleEditMode()
    await nextTick()
    // Nothing written to localStorage for editMode.
    for (let i = 0; i < localStorage.length; i++) {
      expect(localStorage.key(i)).not.toContain('edit-mode')
    }
  })

  it('sets hero-field visibility and persists it to localStorage', async () => {
    const { setHeroFieldVisible, heroFields } = useDocumentLayout()
    setHeroFieldVisible('amount', false)
    expect(heroFields.value.find((f) => f.key === 'amount')?.visible).toBe(false)
    await nextTick() // let useStorage flush
    expect(localStorage.getItem(HERO_FIELDS_STORAGE_KEY)).toContain('amount')
    expect(localStorage.getItem(HERO_FIELDS_STORAGE_KEY)).toContain('false')
  })

  it('moves a hero field and reorders via setHeroFieldOrder', () => {
    const { moveHeroField, setHeroFieldOrder, heroFields } = useDocumentLayout()
    const firstKey = heroFields.value[0]!.key
    moveHeroField(0, 2)
    expect(heroFields.value[2]!.key).toBe(firstKey)

    const reversed = [...heroFields.value].map((f) => f.key).reverse()
    setHeroFieldOrder(reversed)
    expect(heroFields.value.map((f) => f.key)).toEqual(reversed)
  })

  it('moveCard moves a card across columns to the target index', () => {
    const layout = useDocumentLayout()
    layout.resetLayout()
    layout.moveCard('comments', 'right', 1)
    expect(layout.cardColumns.value.left).not.toContain('comments')
    expect(layout.cardColumns.value.right[1]).toBe('comments')
  })

  it('setColumn replaces a column and persists it', async () => {
    const { setColumn, cardColumns } = useDocumentLayout()
    const reversedLeft = [...cardColumns.value.left].reverse()
    setColumn('left', reversedLeft)
    expect(cardColumns.value.left).toEqual(reversedLeft)
    await nextTick()
    expect(localStorage.getItem(CARD_COLUMNS_STORAGE_KEY)).toContain('metadata')
  })

  it('round-trips persisted hero/card state and reconciles a fresh read', async () => {
    const { setHeroFieldVisible, setColumn } = useDocumentLayout()
    setHeroFieldVisible('kind', false)
    setColumn('left', [...DEFAULT_CARD_COLUMNS.left].reverse())
    await nextTick()

    // Simulate a fresh page load: reconcile what is now in localStorage.
    const storedHero = JSON.parse(localStorage.getItem(HERO_FIELDS_STORAGE_KEY)!) as HeroField[]
    const storedColumns = JSON.parse(
      localStorage.getItem(CARD_COLUMNS_STORAGE_KEY)!,
    ) as CardColumns
    const hero = reconcileHeroFields(storedHero, DEFAULT_HERO_FIELDS)
    const columns = reconcileCardColumns(storedColumns, DEFAULT_CARD_COLUMNS)
    expect(hero.find((f) => f.key === 'kind')?.visible).toBe(false)
    expect(columns.left[0]).toBe('history')
  })

  it('resetLayout restores the defaults', () => {
    const { setHeroFieldVisible, setColumn, resetLayout, heroFields, cardColumns } =
      useDocumentLayout()
    setHeroFieldVisible('kind', false)
    setColumn('left', [...DEFAULT_CARD_COLUMNS.left].reverse())
    resetLayout()
    expect(heroFields.value).toEqual(DEFAULT_HERO_FIELDS)
    expect(cardColumns.value).toEqual(DEFAULT_CARD_COLUMNS)
  })
})
