import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import DocumentDetailView from '../DocumentDetailView.vue'
import type { DocumentDetail, DocumentMarkdownResponse } from '@/api/documents'

// pdfjs-dist can't run its worker/canvas in jsdom — mock the whole module
// so that DocumentPdfPreview (now imported by DocumentDetailView) can be loaded.
vi.mock('pdfjs-dist', () => ({
  GlobalWorkerOptions: { workerSrc: '' },
  getDocument: vi.fn(() => ({ promise: new Promise(() => {}), destroy: () => Promise.resolve() })),
}))

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
    validation: null,
    review_status: 'unreviewed',
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
  /** What GET /api/documents/12/markdown returns; tests may override. */
  let markdownResponse: () => Response

  beforeEach(async () => {
    detail = makeDetail()
    patchResponse = () => jsonResponse(detail)
    markdownResponse = () =>
      jsonResponse({ page_count: 1, pages: [{ page_number: 1, markdown: '# Invoice\n\nTotal: €123.45' }] } satisfies DocumentMarkdownResponse)
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
      if (url === '/api/documents/12/markdown' && method === 'GET') {
        return Promise.resolve(markdownResponse())
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

  it('renders the pdf.js preview component for a PDF document', async () => {
    detail = makeDetail({ mime_type: 'application/pdf', has_thumbnail: true })
    const DocumentPdfPreviewStub = { template: '<div />', props: ['src', 'poster', 'openHref', 'downloadHref', 'initialPage'] }
    await router.push('/documents/12')
    wrapper = mount(DocumentDetailView, {
      global: {
        plugins: [router, pinia],
        stubs: { DocumentPdfPreview: DocumentPdfPreviewStub },
      },
    })
    await flushPromises()
    const preview = wrapper.findComponent(DocumentPdfPreviewStub)
    expect(preview.exists()).toBe(true)
    expect(preview.props('src')).toContain('disposition=inline')
    // The legacy native iframe must be gone.
    expect(wrapper.find('iframe').exists()).toBe(false)
  })

  it('preview header opens the inline PDF in a new tab and downloads the searchable PDF', async () => {
    const w = await mountView()
    const open = w.find('[data-testid="preview-open"]')
    expect(open.attributes('href')).toBe('/api/documents/12/searchable.pdf?disposition=inline')
    expect(open.attributes('target')).toBe('_blank')
    // Download is an attachment (no disposition param), preferring the searchable PDF.
    expect(w.find('[data-testid="preview-download"]').attributes('href')).toBe(
      '/api/documents/12/searchable.pdf',
    )
  })

  it('preview header download falls back to the original when there is no searchable PDF', async () => {
    detail = makeDetail({ has_searchable_pdf: false })
    const w = await mountView()
    expect(w.find('[data-testid="preview-download"]').attributes('href')).toBe(
      '/api/documents/12/original',
    )
  })

  it('hero header shows the key stats and the title falls back to a placeholder', async () => {
    detail = makeDetail({ title: null, amount_total: null })
    const w = await mountView()
    expect(w.find('h1').text()).toBe('Untitled document')
    const stats = w.find('[data-testid="hero-stats"]').text()
    expect(stats).toContain('Invoice') // kind
    expect(stats).toContain('Eneco') // sender
    expect(stats).toContain('15 May 2026') // document date
    expect(stats).toContain('—') // amount is null
  })

  it('hero header renders each tag as a coloured badge', async () => {
    detail = makeDetail({
      tags: [
        { slug: 'energie', name: 'Energie' },
        { slug: 'wonen', name: 'Wonen' },
      ],
    })
    const w = await mountView()
    expect(w.findAll('[data-testid="hero-tags"] .rounded-full')).toHaveLength(2)
    expect(w.find('[data-testid="hero-tags"]').text()).toContain('Energie')
  })

  it('passes the thumbnail as poster to DocumentPdfPreview when has_thumbnail is true', async () => {
    detail = makeDetail({ has_thumbnail: true })
    const DocumentPdfPreviewStub = { template: '<div />', props: ['src', 'poster', 'openHref', 'downloadHref', 'initialPage'] }
    await router.push('/documents/12')
    wrapper = mount(DocumentDetailView, {
      global: {
        plugins: [router, pinia],
        stubs: { DocumentPdfPreview: DocumentPdfPreviewStub },
      },
    })
    await flushPromises()
    const preview = wrapper.findComponent(DocumentPdfPreviewStub)
    expect(preview.exists()).toBe(true)
    expect(preview.props('poster')).toBe('/api/documents/12/thumbnail')
    expect(wrapper.find('[data-testid="preview-pdf-image-link"]').exists()).toBe(false)
    expect(wrapper.find('iframe').exists()).toBe(false)
  })

  it('passes undefined poster to DocumentPdfPreview when has_thumbnail is false', async () => {
    detail = makeDetail({ has_thumbnail: false, has_searchable_pdf: false })
    const DocumentPdfPreviewStub = { template: '<div />', props: ['src', 'poster', 'openHref', 'downloadHref', 'initialPage'] }
    await router.push('/documents/12')
    wrapper = mount(DocumentDetailView, {
      global: {
        plugins: [router, pinia],
        stubs: { DocumentPdfPreview: DocumentPdfPreviewStub },
      },
    })
    await flushPromises()
    const preview = wrapper.findComponent(DocumentPdfPreviewStub)
    expect(preview.exists()).toBe(true)
    expect(preview.props('poster')).toBeUndefined()
    expect(wrapper.find('[data-testid="preview-pdf-locked"]').exists()).toBe(false)
    expect(wrapper.find('iframe').exists()).toBe(false)
  })

  it('falls back to the original PDF (no searchable PDF) and then to a no-preview panel', async () => {
    const DocumentPdfPreviewStub = { template: '<div />', props: ['src', 'poster', 'openHref', 'downloadHref', 'initialPage'] }

    detail = makeDetail({ has_searchable_pdf: false })
    await router.push('/documents/12')
    wrapper = mount(DocumentDetailView, {
      global: {
        plugins: [router, pinia],
        stubs: { DocumentPdfPreview: DocumentPdfPreviewStub },
      },
    })
    await flushPromises()
    const preview = wrapper.findComponent(DocumentPdfPreviewStub)
    expect(preview.exists()).toBe(true)
    expect(preview.props('src')).toBe('/api/documents/12/original?disposition=inline')
    wrapper.unmount()

    detail = makeDetail({ mime_type: 'text/plain', has_searchable_pdf: false })
    wrapper = mount(DocumentDetailView, {
      global: { plugins: [router, pinia] },
    })
    await flushPromises()
    expect(wrapper.find('[data-testid="preview-fallback"]').exists()).toBe(true)
    // The fallback's download link keeps the attachment default.
    expect(wrapper.find('[data-testid="preview-fallback"] a').attributes('href')).toBe(
      '/api/documents/12/original',
    )
    wrapper.unmount()

    detail = makeDetail({ mime_type: 'image/jpeg', has_searchable_pdf: false })
    wrapper = mount(DocumentDetailView, {
      global: { plugins: [router, pinia] },
    })
    await flushPromises()
    // Inline: Firefox refuses to render <img> sources served as attachment.
    expect(wrapper.find('[data-testid="preview-image"]').attributes('src')).toBe(
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

  it('shows a warning badge for a flagged field', async () => {
    detail = makeDetail({
      validation: {
        findings: [
          {
            rule: 'amount_grounding',
            field: 'amount_total',
            severity: 'warn',
            message: 'Amount could not be verified against source text',
          },
        ],
      },
    })
    const w = await mountView()
    // The amount row should contain a warning badge with the finding message as title
    const amountRow = w.find('[data-testid="row-amount"]')
    expect(amountRow.exists()).toBe(true)
    const badge = amountRow.find('[data-testid="validation-badge"]')
    expect(badge.exists()).toBe(true)
    expect(badge.attributes('title')).toContain('Amount could not be verified')
  })

  it('renders document-level findings (field: null) in the validation-findings banner', async () => {
    detail = makeDetail({
      review_status: 'needs_review',
      validation: {
        findings: [
          {
            rule: 'empty_extraction',
            field: null,
            severity: 'warn',
            message: 'extraction produced no useful metadata',
          },
        ],
      },
    })
    const w = await mountView()
    // Document-level findings must appear in the distinct validation-findings section.
    const banner = w.find('[data-testid="validation-findings"]')
    expect(banner.exists()).toBe(true)
    expect(banner.text()).toContain('extraction produced no useful metadata')
    // Field-level badge section must NOT be triggered for this finding.
    expect(w.findAll('[data-testid="validation-badge"]')).toHaveLength(0)
    // The action-notice banner (detail-banner) must not be affected.
    expect(w.find('[data-testid="detail-banner"]').exists()).toBe(false)
  })

  it('passes the page query param as initialPage to DocumentPdfPreview', async () => {
    const DocumentPdfPreviewStub = { template: '<div />', props: ['src', 'poster', 'openHref', 'downloadHref', 'initialPage'] }
    await router.push('/documents/12?page=2')
    wrapper = mount(DocumentDetailView, {
      global: {
        plugins: [router, pinia],
        stubs: { DocumentPdfPreview: DocumentPdfPreviewStub },
      },
    })
    await flushPromises()
    const preview = wrapper.findComponent(DocumentPdfPreviewStub)
    expect(preview.exists()).toBe(true)
    expect(preview.props('initialPage')).toBe(2)
    expect(wrapper.find('iframe').exists()).toBe(false)
  })

  it('marks the document verified', async () => {
    const verifiedDetail = makeDetail({ review_status: 'verified' })
    // Stub verifyDocument via fetch: POST /api/documents/12/verify
    fetchMock.mockImplementation((input: unknown, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url === '/api/kinds') return Promise.resolve(jsonResponse(KINDS))
      if (url === '/api/senders') return Promise.resolve(jsonResponse(SENDERS))
      if (url === '/api/documents/12' && method === 'GET')
        return Promise.resolve(jsonResponse(detail))
      if (url === '/api/documents/12/verify' && method === 'POST')
        return Promise.resolve(jsonResponse(verifiedDetail))
      return Promise.resolve(jsonResponse({ detail: `unexpected ${method} ${url}` }, 500))
    })
    const w = await mountView()
    // "Mark verified" button should be visible when review_status !== 'verified'
    const btn = w.find('[data-testid="mark-verified"]')
    expect(btn.exists()).toBe(true)
    await btn.trigger('click')
    await flushPromises()
    // Button should disappear once status is verified
    expect(w.find('[data-testid="mark-verified"]').exists()).toBe(false)
    // Status text should reflect verified
    expect(w.text()).toContain('verified')
  })

  it('lazily fetches markdown on first reveal and renders page content', async () => {
    const w = await mountView()

    // Markdown section should exist but not have fetched yet (details is closed)
    expect(w.find('[data-testid="markdown-details"]').exists()).toBe(true)
    const markdownCalls = () =>
      fetchMock.mock.calls.filter((c) => String(c[0]).endsWith('/markdown'))
    expect(markdownCalls()).toHaveLength(0)

    // Trigger the toggle (reveal) — simulates the user opening the <details>
    await w.find('[data-testid="markdown-details"]').trigger('toggle')
    await flushPromises()

    expect(markdownCalls()).toHaveLength(1)
    // Page content should be rendered
    expect(w.find('[data-testid="markdown-content"]').exists()).toBe(true)
    expect(w.find('[data-testid="markdown-content"]').html()).toContain('Invoice')
  })

  it('does not re-fetch markdown if the section is toggled again', async () => {
    const w = await mountView()
    const details = w.find('[data-testid="markdown-details"]')
    await details.trigger('toggle')
    await flushPromises()
    await details.trigger('toggle')
    await flushPromises()
    const markdownCalls = fetchMock.mock.calls.filter((c) => String(c[0]).endsWith('/markdown'))
    expect(markdownCalls).toHaveLength(1)
  })

  it('shows the empty state when page_count is 0', async () => {
    markdownResponse = () => jsonResponse({ page_count: 0, pages: [] } satisfies DocumentMarkdownResponse)
    const w = await mountView()
    await w.find('[data-testid="markdown-details"]').trigger('toggle')
    await flushPromises()
    expect(w.find('[data-testid="markdown-empty"]').exists()).toBe(true)
    expect(w.find('[data-testid="markdown-content"]').exists()).toBe(false)
  })

  it('renders DocumentSeriesTrend with the loaded document id', async () => {
    const DocumentSeriesTrendStub = { template: '<div />', props: ['documentId'] }
    await router.push('/documents/12')
    wrapper = mount(DocumentDetailView, {
      global: {
        plugins: [router, pinia],
        stubs: { DocumentSeriesTrend: DocumentSeriesTrendStub },
      },
    })
    await flushPromises()
    const trend = wrapper.findComponent(DocumentSeriesTrendStub)
    expect(trend.exists()).toBe(true)
    expect(trend.props('documentId')).toBe(detail.id)
  })
})
