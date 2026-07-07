import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import DocumentFilterBar from '../DocumentFilterBar.vue'
import type { AppliedFilters } from '@/utils/documentQuery'

const EMPTY: AppliedFilters = {
  q: '',
  kind: '',
  senderId: '',
  recipientId: '',
  projects: [],
  tags: [],
  language: '',
  status: '',
  dateFrom: '',
  dateTo: '',
  review: '',
  sort: 'document_date',
  dir: 'desc',
  page: 1,
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const KINDS = [{ slug: 'invoice', name: 'Invoice', document_count: 3 }]
const SENDERS = [{ id: 3, name: 'Eneco', document_count: 3 }]
const RECIPIENTS = [{ id: 5, name: 'John', document_count: 7 }]
const TAGS = [
  { slug: 'energie', name: 'Energie', document_count: 2 },
  { slug: 'wonen', name: 'Wonen', document_count: 1 },
]
const PROJECTS = [
  { slug: 'house-purchase', name: 'House purchase', document_count: 4 },
  { slug: 'taxes', name: 'Taxes', document_count: 2 },
]

function mountBar(applied: AppliedFilters = EMPTY): VueWrapper {
  return mount(DocumentFilterBar, {
    attachTo: document.body,
    props: { applied },
  })
}

describe('DocumentFilterBar', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    // Fresh Pinia per test → a fresh taxonomy-options store (empty cache).
    setActivePinia(createPinia())
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
    fetchMock.mockImplementation((input: unknown) => {
      const url = String(input)
      if (url === '/api/kinds') return Promise.resolve(jsonResponse(KINDS))
      if (url === '/api/senders') return Promise.resolve(jsonResponse(SENDERS))
      if (url === '/api/recipients') return Promise.resolve(jsonResponse(RECIPIENTS))
      if (url === '/api/tags') return Promise.resolve(jsonResponse(TAGS))
      if (url === '/api/projects') return Promise.resolve(jsonResponse(PROJECTS))
      return Promise.resolve(jsonResponse({ detail: `unexpected ${url}` }, 500))
    })
  })

  afterEach(() => {
    document.body.replaceChildren()
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it('initialises the search input from applied.q', () => {
    const w = mountBar({ ...EMPTY, q: 'rekening' })
    expect((w.get('[data-testid="filter-search"]').element as HTMLInputElement).value).toBe(
      'rekening',
    )
  })

  it('emits a debounced replace apply while typing', async () => {
    vi.useFakeTimers()
    const w = mountBar()
    await w.get('[data-testid="filter-search"]').setValue('reken')
    expect(w.emitted('apply')).toBeUndefined() // not yet — debounced
    vi.advanceTimersByTime(300)
    const [query, opts] = w.emitted('apply')![0] as [Record<string, unknown>, { replace: boolean }]
    expect(query).toEqual({ q: 'reken' })
    expect(opts).toEqual({ replace: true })
  })

  it('emits immediately (push) on Enter', async () => {
    const w = mountBar()
    await w.get('[data-testid="filter-search"]').setValue('reken')
    await w.get('[data-testid="filter-search"]').trigger('keydown.enter')
    const [query, opts] = w.emitted('apply')!.at(-1) as [
      Record<string, unknown>,
      { replace: boolean } | undefined,
    ]
    expect(query).toEqual({ q: 'reken' })
    expect(opts?.replace).toBeFalsy()
  })

  it('selecting a kind emits a push apply with the kind slug', async () => {
    const w = mountBar()
    await flushPromises() // taxonomy load
    await w.get('[data-testid="pill-kind"] [data-testid="filter-pill-button"]').trigger('click')
    await w.get('[data-testid="kind-option-invoice"]').trigger('click')
    expect(w.emitted('apply')!.at(-1)![0]).toEqual({ kind: 'invoice' })
  })

  it('selecting a recipient emits a push apply with the recipient id', async () => {
    const w = mountBar()
    await flushPromises() // taxonomy load
    await w
      .get('[data-testid="pill-recipient"] [data-testid="filter-pill-button"]')
      .trigger('click')
    await w.get('[data-testid="recipient-option-5"]').trigger('click')
    expect(w.emitted('apply')!.at(-1)![0]).toEqual({ recipient_id: '5' })
  })

  it('renders a removable recipient chip and clears the recipient on remove', async () => {
    const w = mountBar({ ...EMPTY, recipientId: '5' })
    await flushPromises()
    const chip = w.get('[data-testid="chip-recipient"]')
    expect(chip.text()).toContain('John')
    await w.get('[data-testid="chip-remove-recipient"]').trigger('click')
    expect(w.emitted('apply')!.at(-1)![0]).toEqual({})
  })

  it('selecting projects emits repeated project (OR)', async () => {
    const w = mountBar()
    await flushPromises() // taxonomy load
    await w.get('[data-testid="pill-project"] [data-testid="filter-pill-button"]').trigger('click')
    // AppCheckboxes renders one input per project; check them within the pill panel.
    await w.get('[data-testid="pill-project"]').get('input[value="house-purchase"]').setValue(true)
    await w.get('[data-testid="pill-project"]').get('input[value="taxes"]').setValue(true)
    expect(w.emitted('apply')!.at(-1)![0]).toEqual({ project: ['house-purchase', 'taxes'] })
  })

  it('renders a removable project chip per project and drops it on remove', async () => {
    const w = mountBar({ ...EMPTY, projects: ['house-purchase'] })
    await flushPromises()
    const chip = w.get('[data-testid="chip-project-house-purchase"]')
    expect(chip.text()).toContain('House purchase')
    await w.get('[data-testid="chip-remove-project-house-purchase"]').trigger('click')
    expect(w.emitted('apply')!.at(-1)![0]).toEqual({})
  })

  it('selecting multiple tags emits repeated tag', async () => {
    const w = mountBar()
    await flushPromises()
    // Open the tag pill
    await w.get('[data-testid="pill-tag"] [data-testid="filter-pill-button"]').trigger('click')
    // AppCheckboxes renders first input with id="filter-tags"; the fieldset has no id.
    // Target checkboxes within the tag pill panel instead.
    await w.get('[data-testid="pill-tag"]').get('input[value="energie"]').setValue(true)
    await w.get('[data-testid="pill-tag"]').get('input[value="wonen"]').setValue(true)
    expect(w.emitted('apply')!.at(-1)![0]).toEqual({ tag: ['energie', 'wonen'] })
  })

  it('renders a removable chip per active filter and emits apply without that filter on remove', async () => {
    const w = mountBar({ ...EMPTY, q: 'rekening', kind: 'invoice' })
    await flushPromises()
    // chip-remove-* buttons also start with "chip-"; exclude them so we only
    // count the chip container spans (chip-q, chip-kind).
    const chips = w.findAll('[data-testid^="chip-"]:not([data-testid^="chip-remove-"])')
    expect(chips.length).toBe(2)
    await w.get('[data-testid="chip-remove-kind"]').trigger('click')
    expect(w.emitted('apply')!.at(-1)![0]).toEqual({ q: 'rekening' })
  })

  it('removing one tag keeps the others', async () => {
    const w = mountBar({ ...EMPTY, tags: ['energie', 'wonen'] })
    await flushPromises()
    await w.get('[data-testid="chip-remove-tag-energie"]').trigger('click')
    expect(w.emitted('apply')!.at(-1)![0]).toEqual({ tag: ['wonen'] })
  })

  it('Clear all emits clear', async () => {
    const w = mountBar({ ...EMPTY, q: 'rekening' })
    await w.get('[data-testid="filter-clear-all"]').trigger('click')
    expect(w.emitted('clear')).toHaveLength(1)
  })

  it('the clear-search button empties the input and emits q removed', async () => {
    const w = mountBar({ ...EMPTY, q: 'rekening' })
    await w.get('[data-testid="filter-search-clear"]').trigger('click')
    expect((w.get('[data-testid="filter-search"]').element as HTMLInputElement).value).toBe('')
    expect(w.emitted('apply')!.at(-1)![0]).toEqual({})
  })

  it('resets to page 1 when a filter changes (emitted query has no page)', async () => {
    const w = mountBar({ ...EMPTY, page: 3, kind: 'invoice' })
    await flushPromises()
    await w.get('[data-testid="pill-sender"] [data-testid="filter-pill-button"]').trigger('click')
    await w.get('[data-testid="sender-option-3"]').trigger('click')
    const query = w.emitted('apply')!.at(-1)![0] as Record<string, unknown>
    expect(query.page).toBeUndefined()
    expect(query).toEqual({ kind: 'invoice', sender_id: '3' })
  })

  it('always shows the pill row (no collapse toggle) and lets it wrap', () => {
    const w = mountBar()
    const pills = w.get('[data-testid="filter-pills"]')
    // Visible at every width and wraps onto multiple rows on narrow screens.
    expect(pills.classes()).toContain('flex')
    expect(pills.classes()).toContain('flex-wrap')
    expect(pills.classes()).not.toContain('hidden')
    // The Filters collapse toggle has been removed.
    expect(w.find('[data-testid="filter-toggle"]').exists()).toBe(false)
  })
})
