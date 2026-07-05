import { describe, it, expect, beforeEach } from 'vitest'
import { nextTick } from 'vue'
import {
  useDocumentLayout,
  reconcileHeroFields,
  reconcileCardOrder,
  DEFAULT_HERO_FIELDS,
  DEFAULT_CARD_ORDER,
  HERO_FIELD_LABELS,
  HERO_FIELDS_STORAGE_KEY,
  CARD_ORDER_STORAGE_KEY,
  type HeroField,
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

describe('reconcileCardOrder', () => {
  it('returns the defaults when nothing is stored', () => {
    expect(reconcileCardOrder(null, DEFAULT_CARD_ORDER)).toEqual(DEFAULT_CARD_ORDER)
    expect(reconcileCardOrder([], DEFAULT_CARD_ORDER)).toEqual(DEFAULT_CARD_ORDER)
  })

  it('preserves the saved order of still-valid cards', () => {
    const defaults = ['preview', 'markdown', 'notes']
    const stored = ['notes', 'preview', 'markdown']
    expect(reconcileCardOrder(stored, defaults)).toEqual(['notes', 'preview', 'markdown'])
  })

  it('appends a newly-added card and drops an unknown stored card', () => {
    const defaults = ['preview', 'markdown', 'history']
    const stored = ['markdown', 'preview', 'legacy_card']
    expect(reconcileCardOrder(stored, defaults)).toEqual(['markdown', 'preview', 'history'])
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

  it('exposes the default card order', () => {
    const { cardOrder } = useDocumentLayout()
    expect(cardOrder.value).toEqual([
      'preview',
      'markdown',
      'series-chart',
      'notes',
      'metadata',
      'actions',
      'history',
    ])
  })

  it('provides a human label for every hero field key', () => {
    for (const field of DEFAULT_HERO_FIELDS) {
      expect(HERO_FIELD_LABELS[field.key]).toBeTruthy()
    }
    expect(HERO_FIELD_LABELS.created_at).toBe('Ingested')
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

  it('reorders cards and persists the order', async () => {
    const { setCardOrder, moveCard, cardOrder } = useDocumentLayout()
    const firstId = cardOrder.value[0]!
    moveCard(0, 1)
    expect(cardOrder.value[1]).toBe(firstId)

    const reversed = [...cardOrder.value].reverse()
    setCardOrder(reversed)
    expect(cardOrder.value).toEqual(reversed)
    await nextTick()
    expect(localStorage.getItem(CARD_ORDER_STORAGE_KEY)).toContain('preview')
  })

  it('round-trips persisted hero/card state and reconciles a fresh read', async () => {
    const { setHeroFieldVisible, setCardOrder } = useDocumentLayout()
    setHeroFieldVisible('kind', false)
    setCardOrder([...DEFAULT_CARD_ORDER].reverse())
    await nextTick()

    // Simulate a fresh page load: reconcile what is now in localStorage.
    const storedHero = JSON.parse(localStorage.getItem(HERO_FIELDS_STORAGE_KEY)!) as HeroField[]
    const storedCards = JSON.parse(localStorage.getItem(CARD_ORDER_STORAGE_KEY)!) as string[]
    const hero = reconcileHeroFields(storedHero, DEFAULT_HERO_FIELDS)
    const cards = reconcileCardOrder(storedCards, DEFAULT_CARD_ORDER)
    expect(hero.find((f) => f.key === 'kind')?.visible).toBe(false)
    expect(cards[0]).toBe('history')
  })

  it('resetLayout restores the defaults', () => {
    const { setHeroFieldVisible, setCardOrder, resetLayout, heroFields, cardOrder } =
      useDocumentLayout()
    setHeroFieldVisible('kind', false)
    setCardOrder([...DEFAULT_CARD_ORDER].reverse())
    resetLayout()
    expect(heroFields.value).toEqual(DEFAULT_HERO_FIELDS)
    expect(cardOrder.value).toEqual(DEFAULT_CARD_ORDER)
  })
})
