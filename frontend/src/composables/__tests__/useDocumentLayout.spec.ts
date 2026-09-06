import { describe, it, expect, beforeEach, vi } from 'vitest'
import { nextTick } from 'vue'
import { useMetadataEditMode } from '../useMetadataEditMode'
import {
  useDocumentLayout,
  reconcileHeroFields,
  reconcileCardColumns,
  migrateCardOrderToColumns,
  collapseMetadataCards,
  DEFAULT_HERO_FIELDS,
  DEFAULT_CARD_COLUMNS,
  METADATA_CARD_ID,
  RETIRED_METADATA_CARD_IDS,
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
  // --- The facets card (#139) ------------------------------------------------
  //
  // Facets was a fixed element until #139, then its own card, and from
  // 2026-09-06 a group inside the one `metadata` panel. A stored layout naming
  // it is handled by `collapseMetadataCards` (above), not by reconciliation —
  // reconciliation alone would drop it as unknown and lose its position.

  it('drops a retired metadata tile id that reached it unmigrated', () => {
    // Reconciliation is the LAST line of defence, not the migration: it cannot
    // preserve a position for an id it does not know. This pins the behaviour
    // that makes `collapseMetadataCards` necessary rather than optional.
    const stored = {
      left: ['notes', 'metadata-content', 'metadata-parties', 'comments', 'actions', 'history'],
      right: ['preview', 'markdown'],
    }
    const merged = reconcileCardColumns(stored, DEFAULT_CARD_COLUMNS)
    expect(merged.left).not.toContain('metadata-content')
    expect(merged.left).not.toContain('metadata-parties')
    // …and the panel it should have become is appended at the END, below
    // history — the user-visible symptom the migration exists to prevent.
    expect(merged.left).toEqual(['notes', 'comments', 'actions', 'history', METADATA_CARD_ID])
  })

  it('preserves a migrated layout unchanged', () => {
    const stored = {
      left: ['notes', METADATA_CARD_ID, 'comments', 'actions', 'history'],
      right: ['preview', 'markdown'],
    }
    expect(reconcileCardColumns(stored, DEFAULT_CARD_COLUMNS)).toEqual(stored)
  })

  it('appends missing known cards, drops unknown, de-dupes', () => {
    const stored = { left: [METADATA_CARD_ID, METADATA_CARD_ID, 'ghost'], right: ['preview'] } // dup + unknown, missing several
    const merged = reconcileCardColumns(stored, DEFAULT_CARD_COLUMNS)
    const all = [...merged.left, ...merged.right]
    expect(all).not.toContain('ghost') // unknown dropped
    expect(all.filter((c) => c === METADATA_CARD_ID)).toHaveLength(1) // de-duped
    expect(new Set(all)).toEqual(
      new Set([...DEFAULT_CARD_COLUMNS.left, ...DEFAULT_CARD_COLUMNS.right]),
    ) // every known card present once
    expect(merged.left[0]).toBe(METADATA_CARD_ID) // preserved stored order for survivors
  })
})

describe('collapseMetadataCards', () => {
  // The reverse of the 2026-07-09 split: four per-section tiles plus the Facets
  // card become ONE `metadata` panel. Unlike `facets` becoming a card (#139),
  // reconciliation's landing spot and the intended position do NOT coincide —
  // an unmigrated layout has every old id dropped as unknown and the new panel
  // appended at the column's END. So this is the `migrateMetadataCard` case.
  it('collapses the four tiles and facets into one `metadata` id, in place', () => {
    // The reporting user's actual layout: facets dragged into the RIGHT column.
    const stored: CardColumns = {
      left: [
        'notes',
        'metadata-content',
        'metadata-parties',
        'metadata-financial',
        'metadata-system',
        'comments',
        'actions',
        'history',
      ],
      right: ['preview', 'facets', 'markdown'],
    }
    const collapsed = collapseMetadataCards(stored)
    // The panel takes the FIRST old id's position (index 1), not the column end.
    expect(collapsed.left).toEqual([
      'notes',
      METADATA_CARD_ID,
      'comments',
      'actions',
      'history',
    ])
    // The later `facets` occurrence is dropped, not turned into a second panel.
    expect(collapsed.right).toEqual(['preview', 'markdown'])
  })

  it('is idempotent — it runs on every page load', () => {
    const stored: CardColumns = {
      left: ['notes', 'metadata-content', 'metadata-system', 'comments'],
      right: ['preview', 'facets'],
    }
    const once = collapseMetadataCards(stored)
    expect(collapseMetadataCards(once)).toEqual(once)
  })

  it('keeps the position when the panel is in the right column and facets in the left', () => {
    // First-encountered wins, and columns are walked left-then-right, so a
    // user who put facets high in the LEFT column anchors the panel there.
    const stored: CardColumns = {
      left: ['facets', 'notes'],
      right: ['preview', 'metadata-content', 'markdown'],
    }
    const collapsed = collapseMetadataCards(stored)
    expect(collapsed.left).toEqual([METADATA_CARD_ID, 'notes'])
    expect(collapsed.right).toEqual(['preview', 'markdown'])
  })

  it('still collapses a pre-split layout that holds the legacy `metadata` id', () => {
    // `metadata` IS the collapsed id, so an ancient layout needs no separate
    // migration — this is why `migrateMetadataCard` could be deleted outright.
    const stored: CardColumns = { left: ['notes', 'metadata'], right: ['preview'] }
    expect(collapseMetadataCards(stored)).toEqual(stored)
  })

  it('leaves a layout with no metadata card structurally unchanged', () => {
    const stored: CardColumns = { left: ['notes', 'comments'], right: ['preview'] }
    expect(collapseMetadataCards(stored)).toEqual(stored)
  })

  it('tolerates partial/absent columns', () => {
    expect(collapseMetadataCards(null)).toEqual({ left: [], right: [] })
    expect(collapseMetadataCards({ left: ['metadata-financial'] })).toEqual({
      left: [METADATA_CARD_ID],
      right: [],
    })
  })

  it('names every retired id, so a stale one cannot survive as an unknown card', () => {
    expect([...RETIRED_METADATA_CARD_IDS].sort()).toEqual([
      'facets',
      'metadata-content',
      'metadata-financial',
      'metadata-parties',
      'metadata-system',
    ])
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
    // Legacy user who moved markdown before preview and kept a
    // custom left-column order, with no card-columns key yet.
    localStorage.setItem(
      CARD_ORDER_STORAGE_KEY,
      JSON.stringify([
        'history',
        'metadata',
        'markdown',
        'preview',
        'notes',
        'actions',
        'comments',
      ]),
    )
    const mod = await import('../useDocumentLayout')
    const layout = mod.useDocumentLayout()
    // Right column preserved the customized order (markdown before preview).
    expect(layout.cardColumns.value.right).toEqual(['markdown', 'preview'])
    // Left column preserved the flat order for its members; the legacy single
    // `metadata` card IS the panel id, so it passes through in place.
    expect(layout.cardColumns.value.left).toEqual([
      'history',
      mod.METADATA_CARD_ID,
      'notes',
      'actions',
      'comments',
    ])
  })

  it('appends a known card missing from the legacy flat order to its default column', async () => {
    // Legacy user from before "comments" existed as a card.
    localStorage.setItem(
      CARD_ORDER_STORAGE_KEY,
      JSON.stringify(['history', 'metadata', 'notes', 'actions', 'markdown', 'preview']),
    )
    const mod = await import('../useDocumentLayout')
    const layout = mod.useDocumentLayout()
    expect(layout.cardColumns.value.right).toEqual(['markdown', 'preview'])
    // 'comments' wasn't in the legacy order; reconcileCardColumns appends it
    // to its default (left) column rather than dropping it.
    expect(layout.cardColumns.value.left).toEqual([
      'history',
      mod.METADATA_CARD_ID,
      'notes',
      'actions',
      'comments',
    ])
  })

  it('collapses a returning user’s split tiles back into one panel, in place', async () => {
    // The reporting user's real layout: four tiles high in the left column,
    // facets dragged across into the right. The panel must land where the
    // tiles were (index 1), NOT appended below history.
    localStorage.setItem(
      CARD_COLUMNS_STORAGE_KEY,
      JSON.stringify({
        left: [
          'notes',
          'metadata-content',
          'metadata-parties',
          'metadata-financial',
          'metadata-system',
          'comments',
          'actions',
          'history',
        ],
        right: ['preview', 'facets', 'markdown'],
      }),
    )
    const mod = await import('../useDocumentLayout')
    const layout = mod.useDocumentLayout()
    expect(layout.cardColumns.value.left).toEqual([
      'notes',
      mod.METADATA_CARD_ID,
      'comments',
      'actions',
      'history',
    ])
    // facets was absorbed, not left behind as a second card.
    expect(layout.cardColumns.value.right).toEqual(['preview', 'markdown'])
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
    useMetadataEditMode().setEditMode(false)
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
    expect(HERO_FIELD_LABELS.created_at).toBe('Date added to library')
    expect(HERO_FIELD_LABELS.updated_at).toBe('Last edited')
  })

  // `editMode` used to live here as a second flag beside `useMetadataEditMode`'s.
  // The two merged on 2026-09-06 (one "Edit mode" button); the surviving flag is
  // covered by `useMetadataEditMode.spec.ts`, so its tests are not duplicated here.

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
    // Value-exact, not `toContain('metadata')`: a substring check is satisfied
    // by every id this card has ever had ('metadata', 'metadata-content', a
    // hypothetical 'metadata-panel'), so it passed no matter what was written
    // and proved nothing about persistence.
    expect(JSON.parse(localStorage.getItem(CARD_COLUMNS_STORAGE_KEY) ?? 'null')).toEqual({
      left: reversedLeft,
      right: cardColumns.value.right,
    })
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
    // Assert the reversal round-tripped in full rather than probing index 0,
    // which silently encoded whichever card happened to be last in the
    // defaults (and so broke when #139 appended one).
    expect(columns.left).toEqual([...DEFAULT_CARD_COLUMNS.left].reverse())
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

describe('a stored order naming a removed card', () => {
  it('drops series-chart and keeps every card that still exists', () => {
    // What a user's localStorage holds if they arranged their page before
    // the series chart was removed.
    const stored = {
      left: ['notes', 'comments', 'actions'],
      right: ['preview', 'series-chart', 'markdown'],
    }

    // Two args: the known-id set is built from `defaults`, not from a module
    // constant, so this is exactly what discriminates - before the change
    // 'series-chart' is in DEFAULT_CARD_COLUMNS and survives; after, it is not.
    const result = reconcileCardColumns(stored, DEFAULT_CARD_COLUMNS)

    expect(result.right).not.toContain('series-chart')
    // Order among the survivors is preserved, not reset to the default.
    expect(result.right.filter((id) => id === 'preview' || id === 'markdown')).toEqual([
      'preview',
      'markdown',
    ])
    // Nothing the user had is silently lost, and no known card goes missing.
    expect(result.left).toContain('notes')
    const all = [...result.left, ...result.right]
    for (const id of [...DEFAULT_CARD_COLUMNS.left, ...DEFAULT_CARD_COLUMNS.right]) {
      expect(all).toContain(id)
    }
    // No duplicates across the two columns.
    expect(new Set(all).size).toBe(all.length)
  })
})
