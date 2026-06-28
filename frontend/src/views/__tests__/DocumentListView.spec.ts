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
import { useJobsStore } from '@/stores/jobs'

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
    projects: [],
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
    review_status: 'unreviewed',
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
    is_admin: false,
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
const PROJECTS = [{ slug: 'house-purchase', name: 'House purchase', document_count: 4 }]

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
      if (url === '/api/projects') return Promise.resolve(jsonResponse(PROJECTS))
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

  it('makes the whole card clickable via a stretched title link', async () => {
    listResponse = () => jsonResponse(listBody([makeItem()]))
    const w = await mountView()
    const tile = w.find('[data-testid="doc-card"]')
    // The card is the positioning context; the single title anchor stretches
    // over it (after:absolute after:inset-0) so a tap anywhere navigates.
    expect(tile.classes()).toContain('relative')
    const titleLink = tile.find('.app-doc-card__title a')
    expect(titleLink.classes()).toContain('after:absolute')
    expect(titleLink.classes()).toContain('after:inset-0')
    // Still exactly one anchor — no nested links introduced.
    expect(tile.findAll('a')).toHaveLength(1)
  })

  it('falls back to an untitled label and a placeholder thumbnail', async () => {
    listResponse = () => jsonResponse(listBody([makeItem({ title: null, has_thumbnail: false })]))
    const w = await mountView()
    expect(w.find('.app-doc-card__title a').text()).toBe('Untitled document')
    expect(w.find('.app-doc-card__thumbnail img').exists()).toBe(false)
    // Default fixture is a PDF; with no thumbnail it renders the padlock placeholder.
    expect(w.find('.app-doc-card__thumbnail-fallback').text()).toContain('Protected PDF')
  })

  it('shows a padlock placeholder for a PDF with no thumbnail (password-protected)', async () => {
    listResponse = () =>
      jsonResponse(listBody([makeItem({ has_thumbnail: false, mime_type: 'application/pdf' })]))
    const w = await mountView()
    expect(w.find('[data-testid="thumbnail-locked"]').exists()).toBe(true)
    expect(w.find('.app-doc-card__thumbnail-fallback').text()).toContain('Protected PDF')
  })

  it('shows the plain file-type label for a non-PDF without a thumbnail', async () => {
    listResponse = () =>
      jsonResponse(listBody([makeItem({ has_thumbnail: false, mime_type: 'text/plain' })]))
    const w = await mountView()
    expect(w.find('[data-testid="thumbnail-locked"]').exists()).toBe(false)
    expect(w.find('.app-doc-card__thumbnail-fallback').text()).toBe('Text')
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
    // Deep-linked ?page=2 loads pages 1..2 in one batch at offset 0 (limit 50).
    expect(last.searchParams.get('offset')).toBe('0')
    expect(last.searchParams.get('limit')).toBe('50')
  })

  it('sends the project filter from the URL to the API', async () => {
    await router.push('/?project=house-purchase')
    await mountView()
    await flushPromises()

    const listCall = fetchMock.mock.calls
      .map((c) => String(c[0]))
      .find((url) => url.startsWith('/api/documents'))
    expect(listCall).toBeDefined()
    const params = new URLSearchParams(listCall!.split('?')[1])
    expect(params.get('project')).toBe('house-purchase')
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

  it('links a result to its detail page', async () => {
    listResponse = () => jsonResponse(listBody([makeItem({ snippet: '<b>rekening</b>' })]))
    await router.push('/?q=rekening')
    const w = await mountView()
    expect(w.find('.app-doc-card__title a').attributes('href')).toBe('/documents/12')
  })

  it('renders no old text filter-summary element (the bar replaces it)', async () => {
    listResponse = () => jsonResponse(listBody([makeItem()]))
    const w = await mountView()
    // The DocumentFilterBar replaces the old text-only filter-summary paragraph.
    expect(w.find('[data-testid=”filter-summary”]').exists()).toBe(false)
  })

  // NOTE: “summarises active filters with names resolved from the taxonomy” and
  // “clears all filters from the summary link and refetches unfiltered” were
  // removed because the old text filter-summary / clear-filters link are
  // intentionally replaced by DocumentFilterBar (chips + clear button).
  // Active-filter chip rendering is covered in DocumentFilterBar.spec.ts.

  it('appends a second batch when "Load more" is clicked (offset 25)', async () => {
    // First batch (offset 0) returns a full page of 25; the next offset is the
    // number of loaded items (25). Second batch (offset 25) appends "Second
    // doc". The list must accumulate, not replace.
    const firstBatch = Array.from({ length: 25 }, (_, i) =>
      makeItem({ id: i + 1, title: i === 0 ? 'First doc' : `Doc ${i + 1}` }),
    )
    listResponse = () => jsonResponse(listBody(firstBatch, 60))
    const w = await mountView()

    expect(w.text()).toContain('First doc')
    expect(w.findAll('[data-testid="doc-card"]')).toHaveLength(25)
    const loadMore = w.find('[data-testid="load-more"]')
    expect(loadMore.exists()).toBe(true)

    listResponse = () =>
      jsonResponse(listBody([makeItem({ id: 2, title: 'Second doc' })], 60))
    await loadMore.trigger('click')
    await flushPromises()

    // Both batches present — the list accumulated.
    expect(w.text()).toContain('First doc')
    expect(w.text()).toContain('Second doc')
    expect(w.findAll('[data-testid="doc-card"]')).toHaveLength(26)

    const last = new URL(documentUrls().at(-1)!, 'http://test')
    expect(last.searchParams.get('offset')).toBe('25')
    expect(last.searchParams.get('limit')).toBe('25')
  })

  it('hides "Load more" and stops fetching once all items are loaded', async () => {
    // total === items.length on the first batch: nothing more to load.
    listResponse = () => jsonResponse(listBody([makeItem({ id: 1, title: 'Only doc' })], 1))
    const w = await mountView()

    expect(w.text()).toContain('Only doc')
    expect(w.find('[data-testid="load-more"]').exists()).toBe(false)
    // Exactly one documents fetch (the initial load) — no extra batch attempted.
    expect(documentUrls()).toHaveLength(1)
  })

  it('deep-links ?page=3 by loading the first three pages worth', async () => {
    listResponse = () => jsonResponse(listBody([makeItem({ id: 1 })], 100))
    await router.push('/?page=3')
    await mountView()

    // The first apply fetches with limit 75 (3 * PAGE_SIZE) at offset 0, so a
    // deep-linked page shows everything up to that page.
    const last = new URL(documentUrls().at(-1)!, 'http://test')
    expect(last.searchParams.get('offset')).toBe('0')
    expect(last.searchParams.get('limit')).toBe('75')
  })

  it('resets the list when a filter changes (offset 0, old items gone)', async () => {
    listResponse = () => jsonResponse(listBody([makeItem({ id: 1, title: 'Old doc' })], 60))
    const w = await mountView()
    expect(w.text()).toContain('Old doc')

    // Apply a new filter via the bar — accumulation must reset.
    listResponse = () => jsonResponse(listBody([makeItem({ id: 2, title: 'New doc' })], 60))
    const bar = w.findComponent({ name: 'DocumentFilterBar' })
    bar.vm.$emit('apply', { kind: 'invoice' }, {})
    await flushPromises()

    expect(w.text()).toContain('New doc')
    expect(w.text()).not.toContain('Old doc')
    expect(w.findAll('[data-testid="doc-card"]')).toHaveLength(1)
    const last = new URL(documentUrls().at(-1)!, 'http://test')
    expect(last.searchParams.get('offset')).toBe('0')
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

  it('uses object-cover top crop for the full_width tile preview (default)', async () => {
    listResponse = () => jsonResponse(listBody([makeItem()]))
    const w = await mountView()
    const img = w.find('.app-doc-card__thumbnail img')
    expect(img.classes()).toContain('object-cover')
    expect(img.classes()).toContain('object-top')
    expect(img.classes()).not.toContain('object-contain')
  })

  it('uses object-contain for the whole_page tile preview', async () => {
    useAuthStore().user!.preferences.tile_preview = 'whole_page'
    listResponse = () => jsonResponse(listBody([makeItem()]))
    const w = await mountView()
    const img = w.find('.app-doc-card__thumbnail img')
    expect(img.classes()).toContain('object-contain')
    expect(img.classes()).not.toContain('object-cover')
  })

  it('fades the preview into the body for a full_width thumbnail', async () => {
    listResponse = () => jsonResponse(listBody([makeItem()]))
    const w = await mountView()
    expect(w.find('[data-testid="thumbnail-fade"]').exists()).toBe(true)
  })

  it('omits the fade in whole_page mode (image is letterboxed, not bled to edge)', async () => {
    useAuthStore().user!.preferences.tile_preview = 'whole_page'
    listResponse = () => jsonResponse(listBody([makeItem()]))
    const w = await mountView()
    expect(w.find('[data-testid="thumbnail-fade"]').exists()).toBe(false)
  })

  it('omits the fade when there is no thumbnail to fade', async () => {
    listResponse = () => jsonResponse(listBody([makeItem({ has_thumbnail: false })]))
    const w = await mountView()
    expect(w.find('[data-testid="thumbnail-fade"]').exists()).toBe(false)
  })

  it('sends repeated tag params and status from the URL to the API', async () => {
    await router.push('/?tag=energie&tag=wonen&status=indexed')
    await mountView()
    await flushPromises()

    const listCall = fetchMock.mock.calls
      .map((c) => String(c[0]))
      .find((url) => url.startsWith('/api/documents'))
    expect(listCall).toBeDefined()
    const params = new URLSearchParams(listCall!.split('?')[1])
    expect(params.getAll('tag')).toEqual(['energie', 'wonen'])
    expect(params.get('status')).toBe('indexed')
  })

  it('renders the filter bar and applies its emitted query to the URL', async () => {
    const w = await mountView()
    await flushPromises()
    const bar = w.findComponent({ name: 'DocumentFilterBar' })
    expect(bar.exists()).toBe(true)

    bar.vm.$emit('apply', { kind: 'invoice' }, {})
    await flushPromises()
    expect(router.currentRoute.value.query).toEqual({ kind: 'invoice' })

    bar.vm.$emit('clear')
    await flushPromises()
    expect(router.currentRoute.value.query).toEqual({})
  })

  it('uses router.replace when the bar requests a replace (debounced typing)', async () => {
    await mountView()
    await flushPromises()
    const bar = wrapper!.findComponent({ name: 'DocumentFilterBar' })
    const replaceSpy = vi.spyOn(router, 'replace')
    const pushSpy = vi.spyOn(router, 'push')

    bar.vm.$emit('apply', { q: 'hello' }, { replace: true })
    await flushPromises()

    expect(replaceSpy).toHaveBeenCalledWith({ query: { q: 'hello' } })
    expect(pushSpy).not.toHaveBeenCalled()
    expect(router.currentRoute.value.query).toEqual({ q: 'hello' })
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

  it('requests needs_review documents when the review query is set', async () => {
    await router.push('/?review=needs_review')
    await mountView()
    await flushPromises()

    const listCall = fetchMock.mock.calls
      .map((c) => String(c[0]))
      .find((url) => url.startsWith('/api/documents'))
    expect(listCall).toBeDefined()
    const params = new URLSearchParams(listCall!.split('?')[1])
    expect(params.get('review_status')).toBe('needs_review')
  })

  it('renders a "needs review" badge on a card when review_status is needs_review', async () => {
    listResponse = () =>
      jsonResponse(listBody([makeItem({ review_status: 'needs_review' })]))
    const w = await mountView()
    const card = w.find('[data-testid="doc-card"]')
    expect(card.find('[data-testid="review-badge"]').exists()).toBe(true)
    expect(card.find('[data-testid="review-badge"]').text()).toContain('Needs review')
  })

  it('does not render a review badge when review_status is not needs_review', async () => {
    listResponse = () =>
      jsonResponse(listBody([makeItem({ review_status: 'unreviewed' })]))
    const w = await mountView()
    expect(w.find('[data-testid="review-badge"]').exists()).toBe(false)
  })

  it('renders the document summary on a tile when item.summary is set', async () => {
    listResponse = () =>
      jsonResponse(listBody([makeItem({ summary: 'A short overview of the energy bill.' })]))
    const w = await mountView()
    const summary = w.find('[data-testid="doc-summary"]')
    expect(summary.exists()).toBe(true)
    expect(summary.text()).toBe('A short overview of the energy bill.')
  })

  it('hides the summary in favour of the search snippet when both are present', async () => {
    listResponse = () =>
      jsonResponse(
        listBody([
          makeItem({ summary: 'A short overview.', snippet: 'uw <b>rekening</b>', rank: 0.3 }),
        ]),
      )
    await router.push('/?q=rekening')
    const w = await mountView()
    expect(w.find('[data-testid="doc-summary"]').exists()).toBe(false)
    expect(w.find('.app-doc-card__snippet').exists()).toBe(true)
  })

  it('updates a tile status badge live when the jobs store reports an event', async () => {
    listResponse = () => jsonResponse(listBody([makeItem({ id: 12, status: 'ocr' })]))
    const w = await mountView()
    const tile = (): ReturnType<VueWrapper['find']> => w.find('[data-testid="doc-card"]')
    expect(tile().text()).toContain('Processing')

    const fetchesBefore = documentUrls().length
    const store = useJobsStore()
    store.handle({ document_id: 12, event: 'status_changed', status: 'failed', title: null })
    await flushPromises()

    // The badge flips in place — no extra /api/documents fetch (scroll preserved).
    expect(tile().text()).toContain('Failed')
    expect(tile().text()).not.toContain('Processing')
    expect(documentUrls().length).toBe(fetchesBefore)
  })
})
