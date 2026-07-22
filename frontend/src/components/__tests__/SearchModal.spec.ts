import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import SearchModal from '../SearchModal.vue'

/**
 * jsdom (29.x at the time of writing) implements HTMLDialogElement's `open`
 * property only — `show()`/`showModal()`/`close()` are missing entirely —
 * so the specs stub a minimal happy-path approximation: showModal sets the
 * `open` attribute, close removes it and fires the native `close` event
 * (which is what the component listens to for focus return).
 */
beforeAll(() => {
  if (typeof HTMLDialogElement.prototype.showModal !== 'function') {
    HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) {
      this.setAttribute('open', '')
    }
  }
  if (typeof HTMLDialogElement.prototype.close !== 'function') {
    HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) {
      this.removeAttribute('open')
      this.dispatchEvent(new Event('close'))
    }
  }
})

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const KINDS = [{ slug: 'invoice', name: 'Invoice', document_count: 3 }]
const SENDERS = [{ id: 3, name: 'Eneco', document_count: 3 }]
const TAGS = [{ slug: 'energie', name: 'Energie', document_count: 2 }]
const MATTERS = [
  { slug: 'smith-divorce', name: 'Smith Divorce', document_count: 4 },
  { slug: 'jones-estate', name: 'Jones Estate', document_count: 2 },
]

const Stub = { template: '<div />' }

interface SearchModalExposed {
  open: () => void
}

describe('SearchModal', () => {
  const fetchMock = vi.fn()
  let router: Router
  let wrapper: VueWrapper | undefined

  beforeEach(async () => {
    // Fresh Pinia per test → a fresh taxonomy-options store (empty cache).
    setActivePinia(createPinia())
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
    fetchMock.mockImplementation((input: unknown) => {
      const url = String(input)
      if (url === '/api/kinds') return Promise.resolve(jsonResponse(KINDS))
      if (url === '/api/senders') return Promise.resolve(jsonResponse(SENDERS))
      if (url === '/api/tags') return Promise.resolve(jsonResponse(TAGS))
      if (url === '/api/matters') return Promise.resolve(jsonResponse(MATTERS))
      return Promise.resolve(jsonResponse({ detail: `unexpected ${url}` }, 500))
    })
    router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', name: 'documents', component: Stub },
        { path: '/upload', name: 'upload', component: Stub },
      ],
    })
    await router.push('/')
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = undefined
    document.body.replaceChildren()
    vi.unstubAllGlobals()
  })

  async function mountModal(): Promise<VueWrapper> {
    wrapper = mount(SearchModal, {
      global: { plugins: [router] },
      attachTo: document.body,
    })
    await flushPromises()
    return wrapper
  }

  function exposed(w: VueWrapper): SearchModalExposed {
    return w.vm as unknown as SearchModalExposed
  }

  function taxonomyCalls(): string[] {
    return fetchMock.mock.calls
      .map((call) => String(call[0]))
      .filter((url) => /^\/api\/(kinds|senders|tags)$/.test(url))
  }

  it('opens via the exposed open() method as a labelled dialog', async () => {
    const w = await mountModal()
    const dialog = w.find('dialog')
    expect(dialog.attributes('open')).toBeUndefined()

    exposed(w).open()
    await flushPromises()

    expect(dialog.attributes('open')).toBeDefined()
    expect(dialog.attributes('aria-labelledby')).toBe('search-modal-title')
    expect(w.find('#search-modal-title').text()).toBe('Search your documents')
  })

  it('fetches the taxonomy lazily on first open and caches it', async () => {
    const w = await mountModal()
    expect(taxonomyCalls()).toEqual([])

    exposed(w).open()
    await flushPromises()
    expect(taxonomyCalls().sort()).toEqual(['/api/kinds', '/api/senders', '/api/tags'])

    const kindOptions = w.find('#filter-kind').findAll('option')
    expect(kindOptions.map((option) => option.text())).toEqual(['All kinds', 'Invoice'])
    expect(w.find('#filter-sender').findAll('option').map((o) => o.text())).toEqual([
      'All senders',
      'Eneco',
    ])
    expect(w.find('#filter-tag').findAll('option').map((o) => o.text())).toEqual([
      'All tags',
      'Energie',
    ])

    w.find('dialog').element.close()
    exposed(w).open()
    await flushPromises()
    expect(taxonomyCalls()).toHaveLength(3) // cached: no refetch
  })

  it('pre-fills the form from the current route query', async () => {
    await router.push(
      '/?q=rekening&kind=invoice&sender_id=3&tag=energie&language=nld&date_from=2026-05-01',
    )
    const w = await mountModal()
    exposed(w).open()
    await flushPromises()

    expect((w.find('#search').element as HTMLInputElement).value).toBe('rekening')
    expect((w.find('#filter-kind').element as HTMLSelectElement).value).toBe('invoice')
    expect((w.find('#filter-sender').element as HTMLSelectElement).value).toBe('3')
    expect((w.find('#filter-tag').element as HTMLSelectElement).value).toBe('energie')
    expect((w.find('#filter-language').element as HTMLSelectElement).value).toBe('nld')
    expect((w.find('#filter-date-from-day').element as HTMLInputElement).value).toBe('1')
    expect((w.find('#filter-date-from-month').element as HTMLInputElement).value).toBe('5')
    expect((w.find('#filter-date-from-year').element as HTMLInputElement).value).toBe('2026')
  })

  it('submit pushes the query to the documents route and closes', async () => {
    const w = await mountModal()
    exposed(w).open()
    await flushPromises()

    await w.find('#search').setValue('rekening')
    await w.find('#filter-kind').setValue('invoice')
    await w.find('#filter-language').setValue('nld')
    await w.find('#filter-date-from-day').setValue('1')
    await w.find('#filter-date-from-month').setValue('5')
    await w.find('#filter-date-from-year').setValue('2026')
    await w.find('form[role="search"]').trigger('submit')
    await flushPromises()

    expect(router.currentRoute.value.name).toBe('documents')
    expect(router.currentRoute.value.query).toEqual({
      q: 'rekening',
      kind: 'invoice',
      language: 'nld',
      date_from: '2026-05-01',
    })
    expect(w.find('dialog').attributes('open')).toBeUndefined()
  })

  it('Clear empties every field without navigating or closing', async () => {
    await router.push('/?q=rekening&kind=invoice&sender_id=3')
    const w = await mountModal()
    exposed(w).open()
    await flushPromises()

    await w.find('[data-testid="modal-clear"]').trigger('click')

    expect((w.find('#search').element as HTMLInputElement).value).toBe('')
    expect((w.find('#filter-kind').element as HTMLSelectElement).value).toBe('')
    expect((w.find('#filter-sender').element as HTMLSelectElement).value).toBe('')
    expect(w.find('dialog').attributes('open')).toBeDefined()
    expect(router.currentRoute.value.fullPath).toBe('/?q=rekening&kind=invoice&sender_id=3')
  })

  it('Cancel closes the dialog without navigating', async () => {
    await router.push('/?q=rekening')
    const w = await mountModal()
    exposed(w).open()
    await flushPromises()

    await w.find('[data-testid="modal-cancel"]').trigger('click')

    expect(w.find('dialog').attributes('open')).toBeUndefined()
    expect(router.currentRoute.value.fullPath).toBe('/?q=rekening')
  })

  it('returns focus to the opener on close (incl. native ESC close event)', async () => {
    const opener = document.createElement('button')
    opener.textContent = 'Search'
    document.body.appendChild(opener)
    opener.focus()
    expect(document.activeElement).toBe(opener)

    const w = await mountModal()
    exposed(w).open()
    await flushPromises()

    // ESC ends in the same place as any close: the native `close` event.
    w.find('dialog').element.close()
    expect(document.activeElement).toBe(opener)
  })

  it('the / key opens the modal, but not while typing in a field', async () => {
    const w = await mountModal()

    const input = document.createElement('input')
    document.body.appendChild(input)
    input.focus()
    input.dispatchEvent(new KeyboardEvent('keydown', { key: '/', bubbles: true }))
    await flushPromises()
    expect(w.find('dialog').attributes('open')).toBeUndefined()

    input.blur()
    window.dispatchEvent(new KeyboardEvent('keydown', { key: '/', bubbles: true }))
    await flushPromises()
    expect(w.find('dialog').attributes('open')).toBeDefined()
  })

  it('preserves multiple tags and status when submitting after editing only the query', async () => {
    await router.push('/?tag=energie&tag=wonen&status=indexed&kind=invoice')
    const w = await mountModal()
    exposed(w).open()
    await flushPromises()

    // user edits only the search text
    await w.find('#search').setValue('rekening')
    await w.find('form[role="search"]').trigger('submit')
    await flushPromises()

    const q = router.currentRoute.value.query
    // both tags preserved (array), status preserved, kind preserved, q updated
    expect(q.tag).toEqual(['energie', 'wonen'])
    expect(q.status).toBe('indexed')
    expect(q.kind).toBe('invoice')
    expect(q.q).toBe('rekening')
  })

  it('replaces the tag set when the user picks a single tag in the modal', async () => {
    await router.push('/?tag=energie&tag=wonen')
    const w = await mountModal()
    exposed(w).open()
    await flushPromises()
    // pre-fill is blank for multi-tag; user explicitly selects one
    await w.find('#filter-tag').setValue('energie')
    await w.find('form[role="search"]').trigger('submit')
    await flushPromises()
    // buildDocumentQuery always emits tag as an array (the documented contract);
    // ['energie'] and 'energie' parse identically via parseDocumentQuery.
    expect(router.currentRoute.value.query.tag).toEqual(['energie'])
  })

  it('renders the matter options on open, blank-first', async () => {
    const w = await mountModal()
    exposed(w).open()
    await flushPromises()
    expect(w.find('#filter-matter').findAll('option').map((o) => o.text())).toEqual([
      'All matters',
      'Smith Divorce',
      'Jones Estate',
    ])
  })

  it('pre-fills the matter select from a single-matter query', async () => {
    await router.push('/?matter=smith-divorce')
    const w = await mountModal()
    exposed(w).open()
    await flushPromises()
    expect((w.find('#filter-matter').element as HTMLSelectElement).value).toBe('smith-divorce')
  })

  it('preserves multiple matters when submitting after editing only the query', async () => {
    await router.push('/?matter=smith-divorce&matter=jones-estate&status=indexed')
    const w = await mountModal()
    exposed(w).open()
    await flushPromises()
    // pre-fill is blank for a multi-matter set; user touches only the search text
    expect((w.find('#filter-matter').element as HTMLSelectElement).value).toBe('')

    await w.find('#search').setValue('rekening')
    await w.find('form[role="search"]').trigger('submit')
    await flushPromises()

    const q = router.currentRoute.value.query
    // both matters preserved (OR-composed array), status preserved, q updated
    expect(q.matter).toEqual(['smith-divorce', 'jones-estate'])
    expect(q.status).toBe('indexed')
    expect(q.q).toBe('rekening')
  })

  it('replaces the matter set when the user picks a single matter in the modal', async () => {
    await router.push('/?matter=smith-divorce&matter=jones-estate')
    const w = await mountModal()
    exposed(w).open()
    await flushPromises()
    // pre-fill blank for multi-matter; user explicitly selects one
    await w.find('#filter-matter').setValue('smith-divorce')
    await w.find('form[role="search"]').trigger('submit')
    await flushPromises()
    expect(router.currentRoute.value.query.matter).toEqual(['smith-divorce'])
  })
})
