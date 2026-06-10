import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import DocumentListView from '../DocumentListView.vue'
import type { DocumentListItem } from '@/api/documents'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function makeItem(overrides: Partial<DocumentListItem> = {}): DocumentListItem {
  return {
    id: 12,
    title: 'Energierekening mei 2026',
    summary: null,
    kind: { slug: 'invoice', name: 'Invoice' },
    sender: { id: 3, name: 'Eneco' },
    tags: [],
    document_date: '2026-05-15',
    language: 'nld',
    status: 'indexed',
    mime_type: 'application/pdf',
    page_count: 2,
    created_at: '2026-06-10T12:00:00Z',
    has_searchable_pdf: true,
    has_thumbnail: true,
    snippet: null,
    rank: null,
    ...overrides,
  }
}

function listBody(items: DocumentListItem[], total = items.length) {
  return { items, total, limit: 25, offset: 0 }
}

const Stub = { template: '<div />' }

describe('DocumentListView', () => {
  const fetchMock = vi.fn()
  let router: Router
  let wrapper: VueWrapper | undefined

  beforeEach(async () => {
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
    router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', name: 'documents', component: DocumentListView },
        { path: '/documents/:id', name: 'document-detail', component: Stub },
        { path: '/upload', name: 'upload', component: Stub },
      ],
    })
    await router.push('/')
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = undefined
    vi.unstubAllGlobals()
  })

  async function mountView(): Promise<VueWrapper> {
    wrapper = mount(DocumentListView, { global: { plugins: [router] } })
    await flushPromises()
    return wrapper
  }

  function requestedUrls(): string[] {
    return fetchMock.mock.calls.map((call) => String(call[0]))
  }

  it('renders document rows with title link, kind tag, sender, date and language', async () => {
    fetchMock.mockResolvedValue(jsonResponse(listBody([makeItem()])))
    const w = await mountView()

    const row = w.find('.app-doc-list__item')
    expect(row.exists()).toBe(true)
    const titleLink = row.find('.app-doc-list__title a')
    expect(titleLink.text()).toBe('Energierekening mei 2026')
    expect(titleLink.attributes('href')).toBe('/documents/12')
    expect(row.find('.govuk-tag--blue').text()).toBe('Invoice')
    expect(row.find('.app-doc-list__sender').text()).toBe('Eneco')
    expect(row.find('.app-doc-list__date').text()).toBe('15 May 2026')
    expect(row.find('.govuk-tag--grey').text()).toBe('Dutch')
    expect(row.find('img').attributes('src')).toBe('/api/documents/12/thumbnail')
    expect(w.find('[data-testid="result-count"]').text()).toBe('1 document')
  })

  it('falls back to an untitled label and a placeholder thumbnail', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(listBody([makeItem({ title: null, has_thumbnail: false })])),
    )
    const w = await mountView()
    expect(w.find('.app-doc-list__title a').text()).toBe('Untitled document')
    expect(w.find('.app-doc-list__thumbnail img').exists()).toBe(false)
    expect(w.find('.app-doc-list__thumbnail-fallback').text()).toBe('PDF')
  })

  it('swaps a broken thumbnail for the placeholder on image error', async () => {
    fetchMock.mockResolvedValue(jsonResponse(listBody([makeItem()])))
    const w = await mountView()
    await w.find('.app-doc-list__thumbnail img').trigger('error')
    expect(w.find('.app-doc-list__thumbnail img').exists()).toBe(false)
    expect(w.find('.app-doc-list__thumbnail-fallback').exists()).toBe(true)
  })

  it('shows the empty-library state without filters', async () => {
    fetchMock.mockResolvedValue(jsonResponse(listBody([])))
    const w = await mountView()
    expect(w.find('[data-testid="empty-library"]').text()).toContain(
      'no documents in your library yet',
    )
    expect(w.find('[data-testid="empty-results"]').exists()).toBe(false)
  })

  it('shows the no-results state when a search returns nothing', async () => {
    fetchMock.mockResolvedValue(jsonResponse(listBody([])))
    await router.push('/?q=zonnepanelen')
    const w = await mountView()
    expect(w.find('[data-testid="empty-results"]').text()).toContain('No documents match')
    expect(w.find('[data-testid="empty-library"]').exists()).toBe(false)
  })

  it('submitting a search updates the URL and refetches with q', async () => {
    fetchMock.mockResolvedValue(jsonResponse(listBody([])))
    const w = await mountView()

    await w.find('#search').setValue('rekening')
    await w.find('form[role="search"]').trigger('submit')
    await flushPromises()

    expect(router.currentRoute.value.query.q).toBe('rekening')
    const last = new URL(requestedUrls().at(-1)!, 'http://test')
    expect(last.searchParams.get('q')).toBe('rekening')
    expect(last.searchParams.get('offset')).toBe('0')
  })

  it('renders snippets via renderSnippet: <b> kept, script neutralised', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        listBody([
          makeItem({ snippet: 'uw <b>rekening</b> <script>alert(1)</script>', rank: 0.3 }),
        ]),
      ),
    )
    await router.push('/?q=rekening')
    const w = await mountView()

    const snippet = w.find('.app-doc-list__snippet')
    expect(snippet.findAll('b')).toHaveLength(1)
    expect(snippet.find('b').text()).toBe('rekening')
    expect(snippet.element.querySelector('script')).toBeNull()
    expect(snippet.text()).toContain('<script>alert(1)</script>')
  })

  it('applies kind/language/date filters to the URL and the request', async () => {
    fetchMock.mockResolvedValue(jsonResponse(listBody([])))
    const w = await mountView()

    await w.find('#filter-kind').setValue('invoice')
    await w.find('#filter-language').setValue('nld')
    await w.find('#filter-date-from-day').setValue('1')
    await w.find('#filter-date-from-month').setValue('5')
    await w.find('#filter-date-from-year').setValue('2026')
    await w.find('form[role="search"]').trigger('submit')
    await flushPromises()

    expect(router.currentRoute.value.query).toMatchObject({
      kind: 'invoice',
      language: 'nld',
      date_from: '2026-05-01',
    })
    const last = new URL(requestedUrls().at(-1)!, 'http://test')
    expect(last.searchParams.get('kind')).toBe('invoice')
    expect(last.searchParams.get('language')).toBe('nld')
    expect(last.searchParams.get('date_from')).toBe('2026-05-01')
  })

  it('restores form state and request parameters from the URL', async () => {
    fetchMock.mockResolvedValue(jsonResponse(listBody([])))
    await router.push('/?q=rekening&kind=invoice&page=2')
    const w = await mountView()

    expect((w.find('#search').element as HTMLInputElement).value).toBe('rekening')
    expect((w.find('#filter-kind').element as HTMLSelectElement).value).toBe('invoice')
    const last = new URL(requestedUrls().at(-1)!, 'http://test')
    expect(last.searchParams.get('q')).toBe('rekening')
    expect(last.searchParams.get('kind')).toBe('invoice')
    expect(last.searchParams.get('offset')).toBe('25')
  })

  it('paginates: 60 results make 3 pages and page links update offset', async () => {
    fetchMock.mockResolvedValue(jsonResponse(listBody([makeItem()], 60)))
    const w = await mountView()

    const pagination = w.find('.govuk-pagination')
    expect(pagination.exists()).toBe(true)
    await pagination.find('a[aria-label="Page 2"]').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.query.page).toBe('2')
    const last = new URL(requestedUrls().at(-1)!, 'http://test')
    expect(last.searchParams.get('offset')).toBe('25')
  })

  it('shows a load error message when the API fails', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: 'boom' }, 500))
    const w = await mountView()
    expect(w.find('[data-testid="load-error"]').text()).toContain('could not be loaded')
  })
})
