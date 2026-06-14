import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import DocumentDetailView from '../DocumentDetailView.vue'
import type { DocumentDetail } from '@/api/documents'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function makeDetail(overrides: Partial<DocumentDetail> = {}): DocumentDetail {
  return {
    id: 12,
    title: 'Energierekening mei 2026',
    summary: null,
    kind: { slug: 'invoice', name: 'Invoice' },
    sender: { id: 3, name: 'Eneco' },
    tags: [{ slug: 'energie', name: 'Energie' }],
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
    ocr_text: 'Hierbij ontvangt u de rekeningen voor mei.',
    ocr_confidence: 91.4,
    amount_total: '123.45',
    currency: 'EUR',
    due_date: null,
    expiry_date: null,
    source: 'upload',
    original_filename: 'rekening.pdf',
    sha256: 'abc123',
    extraction: null,
    user_edited_fields: [],
    events: [],
    ...overrides,
  }
}

const KINDS = [
  { slug: 'invoice', name: 'Invoice', document_count: 3 },
  { slug: 'receipt', name: 'Receipt', document_count: 0 },
]
const SENDERS = [{ id: 3, name: 'Eneco', document_count: 3 }]

const Stub = { template: '<div />' }

describe('DocumentDetailView', () => {
  const fetchMock = vi.fn()
  let router: Router
  let pinia: Pinia
  let wrapper: VueWrapper | undefined
  /** What GET /api/documents/12 currently returns; tests mutate this. */
  let detail: DocumentDetail
  /** What PATCH returns; defaults to echoing `detail`. */
  let patchResponse: () => Response

  beforeEach(async () => {
    detail = makeDetail()
    patchResponse = () => jsonResponse(detail)
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
    fetchMock.mockImplementation((input: unknown, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url === '/api/kinds') return Promise.resolve(jsonResponse(KINDS))
      if (url === '/api/senders') return Promise.resolve(jsonResponse(SENDERS))
      if (url === '/api/documents/12/extract' && method === 'POST') {
        return Promise.resolve(jsonResponse({ queued: true, job_id: 1 }, 202))
      }
      if (url === '/api/documents/12' && method === 'GET') {
        return Promise.resolve(jsonResponse(detail))
      }
      if (url === '/api/documents/12' && method === 'PATCH') {
        return Promise.resolve(patchResponse())
      }
      return Promise.resolve(jsonResponse({ detail: `unexpected ${method} ${url}` }, 500))
    })
    pinia = createPinia()
    setActivePinia(pinia)
    router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', name: 'documents', component: Stub },
        { path: '/documents/:id', name: 'document-detail', component: DocumentDetailView },
        { path: '/documents/:id/delete', name: 'document-delete', component: Stub },
      ],
    })
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = undefined
    vi.unstubAllGlobals()
  })

  async function mountView(
    path = '/documents/12',
    props: Record<string, unknown> = {},
  ): Promise<VueWrapper> {
    await router.push(path)
    wrapper = mount(DocumentDetailView, { props, global: { plugins: [router, pinia] } })
    await flushPromises()
    return wrapper
  }

  function rowValue(w: VueWrapper, field: string): string {
    return w.find(`[data-testid="row-${field}"] [data-testid="row-value"]`).text()
  }

  function patchCalls(): { url: string; body: Record<string, unknown> }[] {
    return fetchMock.mock.calls
      .filter((call) => (call[1] as RequestInit | undefined)?.method === 'PATCH')
      .map((call) => ({
        url: String(call[0]),
        body: JSON.parse(String((call[1] as RequestInit).body)) as Record<string, unknown>,
      }))
  }

  it('renders summary rows with values, and a dash for null fields', async () => {
    detail = makeDetail({ title: null, summary: null, due_date: null, ocr_confidence: null })
    const w = await mountView()

    expect(rowValue(w, 'title')).toBe('—')
    expect(rowValue(w, 'kind')).toBe('Invoice')
    expect(rowValue(w, 'sender')).toBe('Eneco')
    expect(rowValue(w, 'document_date')).toBe('15 May 2026')
    expect(rowValue(w, 'language')).toBe('Dutch')
    expect(rowValue(w, 'tags')).toBe('Energie')
    expect(rowValue(w, 'amount')).toBe('123.45 EUR')
    expect(rowValue(w, 'due_date')).toBe('—')
    expect(rowValue(w, 'summary')).toBe('—')
    // Read-only rows: status, OCR confidence (dash when null), source.
    const text = w.text()
    expect(text).toContain('indexed')
    expect(text).toContain('Upload')
    expect(w.find('h1').text()).toBe('Untitled document')
  })

  it('Change → edit → Save PATCHes only that field and shows a success banner', async () => {
    const w = await mountView()
    patchResponse = () => jsonResponse(makeDetail({ title: 'Nieuwe titel' }))

    await w.find('[data-testid="row-title"] .app-link-button').trigger('click')
    await w.find('#edit-title').setValue('Nieuwe titel')
    await w.find('[data-testid="row-title"] form').trigger('submit')
    await flushPromises()

    const calls = patchCalls()
    expect(calls).toHaveLength(1)
    expect(calls[0]!.url).toBe('/api/documents/12')
    expect(calls[0]!.body).toEqual({ title: 'Nieuwe titel' }) // exactly one field
    expect(w.find('[data-testid="detail-banner"]').text()).toContain('Title updated.')
    expect(w.find('h1').text()).toBe('Nieuwe titel')
    expect(w.find('#edit-title').exists()).toBe(false) // editor closed
  })

  it('kind editor offers the fetched kind options and PATCHes kind_slug', async () => {
    const w = await mountView()
    patchResponse = () =>
      jsonResponse(makeDetail({ kind: { slug: 'receipt', name: 'Receipt' } }))

    await w.find('[data-testid="row-kind"] .app-link-button').trigger('click')
    const options = w.find('#edit-kind').findAll('option')
    expect(options.map((option) => option.text())).toEqual(['Not set', 'Invoice', 'Receipt'])

    await w.find('#edit-kind').setValue('receipt')
    await w.find('[data-testid="row-kind"] form').trigger('submit')
    await flushPromises()

    expect(patchCalls()[0]!.body).toEqual({ kind_slug: 'receipt' })
    expect(rowValue(w, 'kind')).toBe('Receipt')
  })

  it('sender editor has a datalist fed by /api/senders', async () => {
    const w = await mountView()
    await w.find('[data-testid="row-sender"] .app-link-button').trigger('click')
    expect(w.find('#edit-sender').attributes('list')).toBe('sender-options')
    const options = w.find('datalist#sender-options').findAll('option')
    expect(options.map((option) => option.attributes('value'))).toEqual(['Eneco'])
  })

  it('tags editor sends a comma-split full-replacement list', async () => {
    const w = await mountView()
    await w.find('[data-testid="row-tags"] .app-link-button').trigger('click')
    await w.find('#edit-tags').setValue(' energie,  wonen ,')
    await w.find('[data-testid="row-tags"] form').trigger('submit')
    await flushPromises()
    expect(patchCalls()[0]!.body).toEqual({ tags: ['energie', 'wonen'] })
  })

  it('shows an error summary and keeps the editor open on a 422', async () => {
    const w = await mountView()
    patchResponse = () => jsonResponse({ detail: "unknown kind slug: 'bogus'" }, 422)

    await w.find('[data-testid="row-kind"] .app-link-button').trigger('click')
    await w.find('[data-testid="row-kind"] form').trigger('submit')
    await flushPromises()

    const summary = w.find('[data-testid="error-summary"]')
    expect(summary.exists()).toBe(true)
    expect(summary.text()).toContain('unknown kind slug')
    expect(w.find('#edit-kind').exists()).toBe(true)
    expect(w.find('[data-testid="detail-banner"]').exists()).toBe(false)
  })

  it('previews the searchable PDF inline in an iframe with an open-in-new-tab link', async () => {
    const w = await mountView()
    // disposition=inline: an attachment response would blank the iframe and
    // trigger a download instead of rendering the PDF.
    // #view=FitH fits the page to the iframe width so a portrait page is not
    // clipped on a narrow (mobile) viewport; the open-in-new-tab link stays plain.
    expect(w.find('[data-testid="preview-pdf"]').attributes('src')).toBe(
      '/api/documents/12/searchable.pdf?disposition=inline#view=FitH',
    )
    expect(w.find('a[target="_blank"]').attributes('href')).toBe(
      '/api/documents/12/searchable.pdf?disposition=inline',
    )
  })

  it('shows a fit-width first-page thumbnail on mobile and keeps the iframe for lg+', async () => {
    const w = await mountView()
    const img = w.find('[data-testid="preview-pdf-image"]')
    expect(img.exists()).toBe(true)
    expect(img.attributes('src')).toBe('/api/documents/12/thumbnail')
    // The thumbnail is wrapped in a link that opens the PDF (same as the text
    // link), shown only below lg.
    const link = w.find('[data-testid="preview-pdf-image-link"]')
    expect(link.attributes('href')).toBe('/api/documents/12/searchable.pdf?disposition=inline')
    expect(link.attributes('target')).toBe('_blank')
    expect(link.classes()).toContain('lg:hidden')
    // The native iframe is kept but hidden below lg.
    expect(w.find('[data-testid="preview-pdf"]').classes()).toContain('hidden')
    expect(w.find('[data-testid="preview-pdf"]').classes()).toContain('lg:block')
  })

  it('keeps the iframe at all sizes for a PDF with no thumbnail', async () => {
    detail = makeDetail({ has_thumbnail: false })
    const w = await mountView()
    expect(w.find('[data-testid="preview-pdf-image"]').exists()).toBe(false)
    const iframe = w.find('[data-testid="preview-pdf"]')
    expect(iframe.exists()).toBe(true)
    expect(iframe.classes()).not.toContain('hidden')
  })

  it('falls back to the original PDF and then to a no-preview panel', async () => {
    detail = makeDetail({ has_searchable_pdf: false })
    let w = await mountView()
    expect(w.find('[data-testid="preview-pdf"]').attributes('src')).toBe(
      '/api/documents/12/original?disposition=inline#view=FitH',
    )
    w.unmount()

    detail = makeDetail({ mime_type: 'text/plain', has_searchable_pdf: false })
    w = await mountView()
    expect(w.find('[data-testid="preview-fallback"]').exists()).toBe(true)
    // The fallback's download link keeps the attachment default.
    expect(w.find('[data-testid="preview-fallback"] a').attributes('href')).toBe(
      '/api/documents/12/original',
    )

    detail = makeDetail({ mime_type: 'image/jpeg', has_searchable_pdf: false })
    wrapper?.unmount()
    w = await mountView()
    // Inline: Firefox refuses to render <img> sources served as attachment.
    expect(w.find('[data-testid="preview-image"]').attributes('src')).toBe(
      '/api/documents/12/original?disposition=inline',
    )
  })

  it('highlights ?highlight= matches in the OCR text safely', async () => {
    detail = makeDetail({ ocr_text: 'De rekeningen <script>alert(1)</script> voor mei.' })
    const w = await mountView('/documents/12?highlight=rekening')

    const pre = w.find('[data-testid="ocr-text"]')
    expect(pre.findAll('mark')).toHaveLength(1)
    expect(pre.find('mark').text()).toBe('rekening')
    expect(pre.element.querySelector('script')).toBeNull()
    expect(w.find('[data-testid="ocr-details"]').attributes('open')).toBeDefined()
  })

  it('re-run extraction polls until the provenance changes, then stops', async () => {
    detail = makeDetail({ extraction: null, events: [] })
    const w = await mountView('/documents/12', { pollIntervalMs: 0, extractTimeoutMs: 5000 })

    await w.find('[data-testid="rerun-extraction"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="detail-banner"]').text()).toContain('Extraction queued')

    // The worker "finishes": the next GET carries new provenance.
    detail = makeDetail({
      extraction: { model: 'claude-haiku-4-5', prompt_version: 2 },
      events: [{ event: 'extraction_completed', detail: {}, created_at: '2026-06-10T13:00:00Z' }],
    })

    await vi.waitFor(() => {
      expect(w.find('[data-testid="detail-banner"]').text()).toContain('Extraction finished')
    })
    const callsAfterSuccess = fetchMock.mock.calls.length
    await flushPromises()
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(fetchMock.mock.calls.length).toBe(callsAfterSuccess) // polling stopped
    expect(w.find('[data-testid="extraction-details"]').text()).toContain('claude-haiku-4-5')
  })

  it('shows downloads and the delete link routing to the confirmation page', async () => {
    const w = await mountView()
    // Download links keep the attachment default — no disposition param.
    expect(w.find('[data-testid="download-original"]').attributes('href')).toBe(
      '/api/documents/12/original',
    )
    expect(w.find('[data-testid="download-searchable"]').attributes('href')).toBe(
      '/api/documents/12/searchable.pdf',
    )
    expect(w.find('[data-testid="delete-link"]').attributes('href')).toBe('/documents/12/delete')
  })

  it('shows the not-found state for a 404', async () => {
    fetchMock.mockImplementation(() =>
      Promise.resolve(jsonResponse({ detail: 'document not found' }, 404)),
    )
    const w = await mountView('/documents/999')
    expect(w.text()).toContain('Document not found')
  })
})
