import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import DocumentListView from '../DocumentListView.vue'
import type { DocumentListItem } from '@/api/documents'
import type { DashboardField } from '@/api/settings'
import { resetTaxonomyOptionsForTests } from '@/composables/taxonomyOptions'
import { useFlashStore } from '@/stores/flash'
import { useAuthStore } from '@/stores/auth'

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
    amount_total: null,
    currency: null,
    snippet: null,
    rank: null,
    ...overrides,
  }
}

/** Seed the active auth store with a user whose dashboard_fields match `fields`. */
function seedPrefs(fields: DashboardField[]): void {
  const auth = useAuthStore()
  auth.user = {
    id: 1,
    username: 'a',
    display_name: 'A',
    preferences: { dashboard_fields: fields },
  }
}

function listBody(items: DocumentListItem[], total = items.length) {
  return { items, total, limit: 25, offset: 0 }
}

const KINDS = [
  { slug: 'invoice', name: 'Invoice', document_count: 3 },
  { slug: 'receipt', name: 'Receipt', document_count: 0 },
]
const SENDERS = [{ id: 3, name: 'Eneco', document_count: 3 }]
const TAGS = [{ slug: 'energie', name: 'Energie', document_count: 2 }]

const Stub = { template: '<div />' }

describe('DocumentListView', () => {
  const fetchMock = vi.fn()
  let router: Router
  let pinia: Pinia
  let wrapper: VueWrapper | undefined
  /** What GET /api/documents responds with; tests override per case. */
  let listResponse: () => Response

  beforeEach(async () => {
    resetTaxonomyOptionsForTests()
    listResponse = () => jsonResponse(listBody([]))
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
    fetchMock.mockImplementation((input: unknown) => {
      const url = String(input)
      if (url === '/api/kinds') return Promise.resolve(jsonResponse(KINDS))
      if (url === '/api/senders') return Promise.resolve(jsonResponse(SENDERS))
      if (url === '/api/tags') return Promise.resolve(jsonResponse(TAGS))
      if (url.startsWith('/api/documents')) return Promise.resolve(listResponse())
      return Promise.resolve(jsonResponse({ detail: `unexpected ${url}` }, 500))
    })
    pinia = createPinia()
    setActivePinia(pinia)
    // Seed a full default field set so that tests which assert kind/sender/date
    // see those fields rendered without needing to configure prefs themselves.
    seedPrefs(['kind', 'sender', 'tags', 'date', 'language', 'status', 'amount', 'file_type'])
    router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', name: 'documents', component: DocumentListView },
        { path: '/documents/:id', name: 'document-detail', component: Stub },
        { path: '/documents/:id/delete', name: 'document-delete', component: Stub },
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
    wrapper = mount(DocumentListView, { global: { plugins: [router, pinia] } })
    await flushPromises()
    return wrapper
  }

  function documentUrls(): string[] {
    return fetchMock.mock.calls
      .map((call) => String(call[0]))
      .filter((url) => url.startsWith('/api/documents'))
  }

  function taxonomyUrls(): string[] {
    return fetchMock.mock.calls
      .map((call) => String(call[0]))
      .filter((url) => /^\/api\/(kinds|senders|tags)$/.test(url))
  }

  it('renders tiles in the dashboard grid with title link, tags, sender and date', async () => {
    listResponse = () => jsonResponse(listBody([makeItem()]))
    const w = await mountView()

    expect(w.find('h1').text()).toBe('Documents')
    expect(w.find('ul.app-doc-grid').exists()).toBe(true)
    const tile = w.find('[data-testid="doc-card"]')
    expect(tile.exists()).toBe(true)
    // Exactly ONE anchor per tile (the title link).
    expect(tile.findAll('a')).toHaveLength(1)
    const titleLink = tile.find('.app-doc-card__title a')
    expect(titleLink.text()).toBe('Energierekening mei 2026')
    expect(titleLink.attributes('href')).toBe('/documents/12')
    expect(titleLink.classes()).toContain('text-violet-600')
    expect(tile.find('.app-doc-card__meta').text()).toContain('Invoice')
    expect(tile.find('.app-doc-card__meta').text()).toContain('Dutch')
    expect(tile.find('.app-doc-card__sender').text()).toBe('Eneco')
    expect(tile.find('.app-doc-card__date').text()).toBe('15 May 2026')
    expect(tile.find('.app-doc-card__thumbnail img').attributes('src')).toBe(
      '/api/documents/12/thumbnail',
    )
    expect(w.find('[data-testid="result-count"]').text()).toBe('1 document')
  })

  it('falls back to an untitled label and a placeholder thumbnail', async () => {
    listResponse = () => jsonResponse(listBody([makeItem({ title: null, has_thumbnail: false })]))
    const w = await mountView()
    expect(w.find('.app-doc-card__title a').text()).toBe('Untitled document')
    expect(w.find('.app-doc-card__thumbnail img').exists()).toBe(false)
    expect(w.find('.app-doc-card__thumbnail-fallback').text()).toBe('PDF')
  })

  it('swaps a broken thumbnail for the placeholder on image error', async () => {
    listResponse = () => jsonResponse(listBody([makeItem()]))
    const w = await mountView()
    await w.find('.app-doc-card__thumbnail img').trigger('error')
    expect(w.find('.app-doc-card__thumbnail img').exists()).toBe(false)
    expect(w.find('.app-doc-card__thumbnail-fallback').exists()).toBe(true)
  })

  it('shows the empty-library state without filters', async () => {
    const w = await mountView()
    expect(w.find('[data-testid="empty-library"]').text()).toContain(
      'no documents in your library yet',
    )
    expect(w.find('[data-testid="empty-results"]').exists()).toBe(false)
  })

  it('shows the no-results state when a search returns nothing', async () => {
    await router.push('/?q=zonnepanelen')
    const w = await mountView()
    expect(w.find('[data-testid="empty-results"]').text()).toContain('No documents match')
    expect(w.find('[data-testid="empty-library"]').exists()).toBe(false)
  })

  it('fetches with the URL query parameters applied', async () => {
    await router.push('/?q=rekening&kind=invoice&sender_id=3&tag=energie&page=2')
    await mountView()

    const last = new URL(documentUrls().at(-1)!, 'http://test')
    expect(last.searchParams.get('q')).toBe('rekening')
    expect(last.searchParams.get('kind')).toBe('invoice')
    expect(last.searchParams.get('sender_id')).toBe('3')
    expect(last.searchParams.getAll('tag')).toEqual(['energie'])
    expect(last.searchParams.get('offset')).toBe('25')
  })

  it('renders snippets via renderSnippet: <b> kept, script neutralised', async () => {
    listResponse = () =>
      jsonResponse(
        listBody([
          makeItem({ snippet: 'uw <b>rekening</b> <script>alert(1)</script>', rank: 0.3 }),
        ]),
      )
    await router.push('/?q=rekening')
    const w = await mountView()

    const snippet = w.find('.app-doc-card__snippet')
    expect(snippet.findAll('b')).toHaveLength(1)
    expect(snippet.find('b').text()).toBe('rekening')
    expect(snippet.element.querySelector('script')).toBeNull()
    expect(snippet.text()).toContain('<script>alert(1)</script>')
  })

  it('passes the active search to detail links as ?highlight=', async () => {
    listResponse = () => jsonResponse(listBody([makeItem({ snippet: '<b>rekening</b>' })]))
    await router.push('/?q=rekening')
    const w = await mountView()
    expect(w.find('.app-doc-card__title a').attributes('href')).toBe(
      '/documents/12?highlight=rekening',
    )
  })

  it('shows no filter summary when nothing is filtered', async () => {
    listResponse = () => jsonResponse(listBody([makeItem()]))
    const w = await mountView()
    expect(w.find('[data-testid="filter-summary"]').exists()).toBe(false)
    // No taxonomy fetch either: names are only needed for active filters.
    expect(taxonomyUrls()).toEqual([])
  })

  it('summarises active filters with names resolved from the taxonomy', async () => {
    listResponse = () => jsonResponse(listBody([makeItem()]))
    await router.push(
      '/?q=rekening&kind=invoice&sender_id=3&tag=energie&language=nld&date_from=2026-05-01&date_to=2026-05-31',
    )
    const w = await mountView()

    const summary = w.find('[data-testid="filter-summary"]')
    expect(summary.text()).toContain('search “rekening”')
    expect(summary.text()).toContain('kind Invoice')
    expect(summary.text()).toContain('sender Eneco')
    expect(summary.text()).toContain('tag Energie')
    expect(summary.text()).toContain('language Dutch')
    expect(summary.text()).toContain('dated from 1 May 2026')
    expect(summary.text()).toContain('dated to 31 May 2026')
  })

  it('clears all filters from the summary link and refetches unfiltered', async () => {
    listResponse = () => jsonResponse(listBody([makeItem()]))
    await router.push('/?q=rekening&kind=invoice')
    const w = await mountView()

    await w.find('[data-testid="clear-filters"]').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.query).toEqual({})
    const last = new URL(documentUrls().at(-1)!, 'http://test')
    expect(last.searchParams.get('q')).toBeNull()
    expect(last.searchParams.get('kind')).toBeNull()
    expect(w.find('[data-testid="filter-summary"]').exists()).toBe(false)
  })

  it('paginates: 60 results make 3 pages and page links update offset', async () => {
    listResponse = () => jsonResponse(listBody([makeItem()], 60))
    const w = await mountView()

    const pagination = w.find('nav[aria-label="Pagination"]')
    expect(pagination.exists()).toBe(true)
    await pagination.find('button[aria-label="Page 2"]').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.query.page).toBe('2')
    const last = new URL(documentUrls().at(-1)!, 'http://test')
    expect(last.searchParams.get('offset')).toBe('25')
  })

  it('keeps the active filters in the URL when changing page', async () => {
    listResponse = () => jsonResponse(listBody([makeItem()], 60))
    await router.push('/?q=rekening&kind=invoice')
    const w = await mountView()

    await w.find('nav[aria-label="Pagination"] button[aria-label="Page 2"]').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.query).toMatchObject({
      q: 'rekening',
      kind: 'invoice',
      page: '2',
    })
  })

  it('shows a load error message when the API fails', async () => {
    listResponse = () => jsonResponse({ detail: 'boom' }, 500)
    const w = await mountView()
    expect(w.find('[data-testid="load-error"]').text()).toContain('could not be loaded')
  })

  it('shows the flash message once after a redirect (e.g. delete)', async () => {
    useFlashStore().set('Energierekening mei 2026 has been deleted.')
    const w = await mountView()
    const banner = w.find('[data-testid="flash-banner"]')
    expect(banner.text()).toContain('has been deleted')
    expect(useFlashStore().message).toBeNull() // consumed: a refresh won't re-show it
  })

  it('renders only the toggled-on fields', async () => {
    seedPrefs(['kind'])
    listResponse = () =>
      jsonResponse(
        listBody([makeItem({ sender: { id: 3, name: 'Eneco' }, document_date: '2026-05-15' })]),
      )
    const w = await mountView()
    expect(w.text()).toContain('Invoice')
    expect(w.find('.app-doc-card__sender').exists()).toBe(false)
    expect(w.find('.app-doc-card__date').exists()).toBe(false)
  })

  it('caps tag chips with a +N overflow', async () => {
    seedPrefs(['tags'])
    listResponse = () =>
      jsonResponse(
        listBody([
          makeItem({
            tags: [
              { slug: 'a', name: 'A' },
              { slug: 'b', name: 'B' },
              { slug: 'c', name: 'C' },
              { slug: 'd', name: 'D' },
              { slug: 'e', name: 'E' },
              { slug: 'f', name: 'F' },
            ],
          }),
        ]),
      )
    const w = await mountView()
    // 6 tags, MAX 4 shown — the overflow counter must read "+2"
    expect(w.find('[data-testid="doc-tags"]').exists()).toBe(true)
    expect(w.find('[data-testid="doc-tags"]').text()).toContain('+2')
    // Only 4 chips rendered (AppBadge renders a rounded-full span per chip).
    expect(w.findAll('[data-testid="doc-tags"] .rounded-full')).toHaveLength(4)
  })
})
