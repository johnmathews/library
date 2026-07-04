import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import DocumentListView from '../DocumentListView.vue'
import type { DocumentListItem } from '@/api/documents'
import type { DashboardField } from '@/api/settings'
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
    recipient: { id: 5, name: 'John' },
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
    review_findings: [],
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
const RECIPIENTS = [{ id: 5, name: 'John', document_count: 7 }]
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
  /** What the "needs review" count probe (review_status=needs_review&limit=1)
   *  responds with; defaults to zero so the button is hidden unless a test opts in. */
  let reviewCountResponse: () => Response

  beforeEach(async () => {
    // The taxonomy-options cache lives in Pinia now; the fresh `createPinia()`
    // below gives each test an empty cache (no explicit reset needed).
    listResponse = () => jsonResponse(listBody([]))
    reviewCountResponse = () => jsonResponse(listBody([], 0))
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
    fetchMock.mockImplementation((input: unknown, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/kinds') return Promise.resolve(jsonResponse(KINDS))
      if (url === '/api/senders') return Promise.resolve(jsonResponse(SENDERS))
      if (url === '/api/recipients') return Promise.resolve(jsonResponse(RECIPIENTS))
      if (url === '/api/tags') return Promise.resolve(jsonResponse(TAGS))
      if (url === '/api/projects') return Promise.resolve(jsonResponse(PROJECTS))
      if (url.startsWith('/api/settings')) {
        // Echo the persisted field list back (server-cleaned shape).
        const body = init?.body ? JSON.parse(String(init.body)) : {}
        return Promise.resolve(jsonResponse({ dashboard_fields: body.dashboard_fields ?? [] }))
      }
      if (url.startsWith('/api/documents')) {
        // The needs-review count probe is a distinct total-only query.
        if (/review_status=needs_review/.test(url) && /[?&]limit=1(&|$)/.test(url)) {
          return Promise.resolve(reviewCountResponse())
        }
        return Promise.resolve(listResponse())
      }
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
      // Exclude the needs-review count probe (limit=1) so list-fetch assertions
      // (order, count) aren't perturbed by it.
      .filter((url) => !(/review_status=needs_review/.test(url) && /[?&]limit=1(&|$)/.test(url)))
  }

  it('accents a tile with its kind default colour (invoice → sky)', async () => {
    listResponse = () => jsonResponse(listBody([makeItem()])) // makeItem() is an invoice
    const w = await mountView()
    const tile = w.find('[data-testid="doc-card"]')
    expect(tile.classes()).toContain('app-doc-card--accented')
    expect(tile.attributes('style') ?? '').toContain('--card-accent: #56b1f3')
  })

  it('leaves a neutral kind (other) with no border accent', async () => {
    listResponse = () => jsonResponse(listBody([makeItem({ kind: { slug: 'other', name: 'Other' } })]))
    const w = await mountView()
    const tile = w.find('[data-testid="doc-card"]')
    expect(tile.classes()).not.toContain('app-doc-card--accented')
    expect(tile.attributes('style') ?? '').not.toContain('--card-accent')
  })

  it("applies a user's per-kind override over the default palette", async () => {
    useAuthStore().user!.preferences.kind_colors = { invoice: '#112233' }
    listResponse = () => jsonResponse(listBody([makeItem()]))
    const w = await mountView()
    const tile = w.find('[data-testid="doc-card"]')
    expect(tile.attributes('style') ?? '').toContain('--card-accent: #112233')
  })

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

  it('lets the user choose tiles-per-row, persisted to localStorage and applied as a CSS var', async () => {
    localStorage.removeItem('library:doc-grid-cols')
    listResponse = () => jsonResponse(listBody([makeItem()]))
    const w = await mountView()

    const select = w.find('[data-testid="grid-cols-select"]')
    expect(select.exists()).toBe(true)
    // Default is Auto: no override var on the grid (responsive breakpoints win).
    expect(w.find('#dashboard-grid').attributes('style') ?? '').not.toContain('--doc-grid-cols')

    await select.setValue('5')
    await flushPromises()
    expect(w.find('#dashboard-grid').attributes('style')).toContain('--doc-grid-cols: 5')
    expect(localStorage.getItem('library:doc-grid-cols')).toContain('5')
  })

  it('sort control round-trips field + direction through the URL and the request', async () => {
    listResponse = () => jsonResponse(listBody([makeItem()]))
    const w = await mountView()

    // Field select changes the URL and re-fetches with the sort param.
    await w.find('[data-testid="sort-field-select"]').setValue('added_date')
    await flushPromises()
    expect(router.currentRoute.value.query.sort).toBe('added_date')
    expect(documentUrls().at(-1)).toContain('sort=added_date')

    // Direction toggle flips desc -> asc and appears in the URL.
    await w.find('[data-testid="sort-dir-toggle"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.dir).toBe('asc')
    expect(documentUrls().at(-1)).toContain('direction=asc')

    // Back to the default field clears the sort param (canonical URL stays clean).
    await w.find('[data-testid="sort-field-select"]').setValue('document_date')
    await flushPromises()
    expect(router.currentRoute.value.query.sort).toBeUndefined()
  })

  it('disables the sort control while a search query is active (rank wins)', async () => {
    await router.push({ path: '/', query: { q: 'factuur' } })
    listResponse = () => jsonResponse(listBody([makeItem()]))
    const w = await mountView()

    expect(w.find('[data-testid="sort-field-select"]').attributes('disabled')).toBeDefined()
    expect(w.find('[data-testid="sort-dir-toggle"]').attributes('disabled')).toBeDefined()
  })

  it('renders card meta fields in the stored order', async () => {
    listResponse = () => jsonResponse(listBody([makeItem()]))
    seedPrefs(['date', 'kind'])
    const w = await mountView()
    const html = w.find('.app-doc-card__meta').html()
    // date span before kind badge.
    expect(html.indexOf('app-doc-card__date')).toBeLessThan(html.indexOf('Invoice'))

    w.unmount()
    seedPrefs(['kind', 'date'])
    const w2 = await mountView()
    const html2 = w2.find('.app-doc-card__meta').html()
    expect(html2.indexOf('Invoice')).toBeLessThan(html2.indexOf('app-doc-card__date'))
  })

  it('keeps the ungated "Needs review" badge pinned first, outside the ordered fields', async () => {
    listResponse = () => jsonResponse(listBody([makeItem({ review_status: 'needs_review' })]))
    // Even with no fields enabled, the review badge still renders.
    seedPrefs([])
    const w = await mountView()
    const meta = w.find('.app-doc-card__meta')
    expect(meta.find('[data-testid="review-badge"]').exists()).toBe(true)
  })

  it('start-review-queue loads the queue and navigates to the first flagged doc', async () => {
    // Count probe says work exists (button shows); the queue-load query returns
    // the flagged docs so start() has an id to open.
    reviewCountResponse = () => jsonResponse(listBody([], 2))
    const flagged = [
      makeItem({ id: 41, review_status: 'needs_review' }),
      makeItem({ id: 42, review_status: 'needs_review' }),
    ]
    listResponse = () => jsonResponse(listBody(flagged, 2))
    seedPrefs([])
    const w = await mountView()
    const push = vi.spyOn(router, 'push')

    const startBtn = w.find('[data-testid="start-review-queue"]')
    expect(startBtn.exists()).toBe(true)
    await startBtn.trigger('click')
    await flushPromises()

    expect(push).toHaveBeenCalledWith({
      name: 'document-detail',
      params: { id: 41 },
      query: { queue: '1' },
    })
  })

  it('shows a short plain-language reason next to the review badge', async () => {
    listResponse = () =>
      jsonResponse(
        listBody([
          makeItem({
            review_status: 'needs_review',
            review_findings: [
              { rule: 'date_plausibility', field: 'document_date', message: 'future date' },
            ],
          }),
        ]),
      )
    seedPrefs([])
    const w = await mountView()
    const reason = w.find('[data-testid="review-reason"]')
    expect(reason.exists()).toBe(true)
    expect(reason.text()).toBe('Unlikely date')
  })

  it('Fields menu opens a popover and persists a toggle via PUT /api/settings', async () => {
    listResponse = () => jsonResponse(listBody([makeItem()]))
    seedPrefs(['kind'])
    const w = await mountView()

    expect(w.find('[data-testid="dashboard-fields-panel"]').exists()).toBe(false)
    await w.find('[data-testid="dashboard-fields-button"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="dashboard-fields-panel"]').exists()).toBe(true)

    // Enable "sender" (currently off) — persists and updates the store.
    await w.find('[data-testid="dashboard-field-sender"]').setValue(true)
    await flushPromises()

    const settingsPut = fetchMock.mock.calls.find(
      (call) => String(call[0]) === '/api/settings' && (call[1] as RequestInit)?.method === 'PUT',
    )
    expect(settingsPut).toBeTruthy()
    expect(JSON.parse(String((settingsPut![1] as RequestInit).body))).toEqual({
      dashboard_fields: ['kind', 'sender'],
    })
    expect(useAuthStore().dashboardFields).toEqual(['kind', 'sender'])
  })

  it('places the Fields button in the sort/tiles controls row', async () => {
    listResponse = () => jsonResponse(listBody([makeItem()]))
    const w = await mountView()
    // The controls row aligns its items at the bottom (items-end); the Fields
    // button now lives there alongside the sort field + tiles-per-row selects.
    const controls = w
      .findAll('.items-end')
      .find((el) => el.find('[data-testid="grid-cols-select"]').exists())
    expect(controls).toBeTruthy()
    expect(controls!.find('[data-testid="dashboard-fields-button"]').exists()).toBe(true)
    expect(controls!.find('[data-testid="sort-field-select"]').exists()).toBe(true)
  })

  it('shows a red "N documents need review" button with the count when some need review', async () => {
    listResponse = () => jsonResponse(listBody([makeItem()]))
    reviewCountResponse = () => jsonResponse(listBody([], 4))
    const w = await mountView()

    const button = w.find('[data-testid="needs-review-filter"]')
    expect(button.exists()).toBe(true)
    expect(button.text()).toContain('4 documents need review')
    // Pale-red attention styling (not the old pill).
    expect(button.classes()).toContain('bg-red-50')
    expect(button.classes()).toContain('border-red-300')
    expect(button.classes()).not.toContain('rounded-full')
  })

  it('singularises the review count for exactly one document', async () => {
    listResponse = () => jsonResponse(listBody([makeItem()]))
    reviewCountResponse = () => jsonResponse(listBody([], 1))
    const w = await mountView()
    expect(w.find('[data-testid="needs-review-filter"]').text()).toContain('1 document needs review')
  })

  it('hides the review button entirely when nothing needs review', async () => {
    listResponse = () => jsonResponse(listBody([makeItem()]))
    reviewCountResponse = () => jsonResponse(listBody([], 0))
    const w = await mountView()
    expect(w.find('[data-testid="needs-review-filter"]').exists()).toBe(false)
  })

  it('toggles the needs_review filter through the URL when clicked', async () => {
    listResponse = () => jsonResponse(listBody([makeItem()]))
    reviewCountResponse = () => jsonResponse(listBody([], 2))
    const w = await mountView()

    await w.find('[data-testid="needs-review-filter"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.review).toBe('needs_review')

    // Clicking again clears it.
    await w.find('[data-testid="needs-review-filter"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.review).toBeUndefined()
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

  it('shows the generic "File" label for a non-text file without a thumbnail', async () => {
    listResponse = () =>
      jsonResponse(
        listBody([makeItem({ has_thumbnail: false, mime_type: 'application/octet-stream' })]),
      )
    const w = await mountView()
    expect(w.find('[data-testid="thumbnail-locked"]').exists()).toBe(false)
    // A non-text type never renders the metadata facsimile, even with metadata.
    expect(w.find('[data-testid="markdown-preview"]').exists()).toBe(false)
    expect(w.find('.app-doc-card__thumbnail-fallback').text()).toBe('File')
  })

  it('renders a metadata facsimile (not "Text") for a text/markdown tile', async () => {
    listResponse = () =>
      jsonResponse(
        listBody([
          makeItem({
            has_thumbnail: false,
            mime_type: 'text/markdown',
            title: 'Energierekening mei 2026',
            kind: { slug: 'invoice', name: 'Invoice' },
            sender: { id: 3, name: 'Eneco' },
            recipient: { id: 5, name: 'John' },
            document_date: '2026-05-15',
          }),
        ]),
      )
    const w = await mountView()
    const preview = w.find('[data-testid="markdown-preview"]')
    expect(preview.exists()).toBe(true)
    const text = preview.text()
    // Title heading + one line per non-empty field (kind / sender / recipient / date).
    expect(text).toContain('Energierekening mei 2026')
    expect(text).toContain('Invoice')
    expect(text).toContain('Eneco')
    expect(text).toContain('John')
    // The generic "Text" placeholder is gone.
    expect(w.find('.app-doc-card__thumbnail-fallback').text()).not.toBe('Text')
  })

  it('omits empty metadata rows in the facsimile', async () => {
    listResponse = () =>
      jsonResponse(
        listBody([
          makeItem({
            has_thumbnail: false,
            mime_type: 'text/markdown',
            title: 'Just a title',
            kind: null,
            sender: null,
            recipient: null,
            document_date: null,
          }),
        ]),
      )
    const w = await mountView()
    const preview = w.find('[data-testid="markdown-preview"]')
    expect(preview.exists()).toBe(true)
    expect(preview.text()).toContain('Just a title')
    // No dangling field labels when their values are absent.
    expect(preview.text()).not.toContain('From')
    expect(preview.text()).not.toContain('To')
  })

  it('renders a metadata facsimile (not "Text") for a text/plain tile with metadata', async () => {
    listResponse = () =>
      jsonResponse(
        listBody([
          makeItem({
            has_thumbnail: false,
            mime_type: 'text/plain',
            title: 'Meterstanden 2026',
            kind: { slug: 'invoice', name: 'Invoice' },
            sender: { id: 3, name: 'Eneco' },
          }),
        ]),
      )
    const w = await mountView()
    const preview = w.find('[data-testid="markdown-preview"]')
    expect(preview.exists()).toBe(true)
    const text = preview.text()
    expect(text).toContain('Meterstanden 2026')
    expect(text).toContain('Invoice')
    expect(text).toContain('Eneco')
    // The generic "Text" placeholder is gone.
    expect(w.find('.app-doc-card__thumbnail-fallback').text()).not.toBe('Text')
  })

  it('falls through to "Text" for a text tile with no title or metadata', async () => {
    listResponse = () =>
      jsonResponse(
        listBody([
          makeItem({
            has_thumbnail: false,
            mime_type: 'text/plain',
            title: null,
            kind: null,
            sender: null,
            recipient: null,
            document_date: null,
          }),
        ]),
      )
    const w = await mountView()
    // hasPreviewMetadata is false, so no facsimile — just the bare "Text" label.
    expect(w.find('[data-testid="markdown-preview"]').exists()).toBe(false)
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

  it('sends the recipient filter from the URL to the API', async () => {
    await router.push('/?recipient_id=5')
    await mountView()
    await flushPromises()

    const listCall = fetchMock.mock.calls
      .map((c) => String(c[0]))
      .find((url) => url.startsWith('/api/documents'))
    expect(listCall).toBeDefined()
    const params = new URLSearchParams(listCall!.split('?')[1])
    expect(params.get('recipient_id')).toBe('5')
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
