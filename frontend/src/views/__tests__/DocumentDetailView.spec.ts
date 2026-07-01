import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import DocumentDetailView from '../DocumentDetailView.vue'
import type { DocumentDetail, DocumentMarkdownResponse } from '@/api/documents'
import { listNoteVersions, restoreNoteVersion, updateNote } from '@/api/notes'
import { useJobsStore } from '@/stores/jobs'

// pdfjs-dist can't run its worker/canvas in jsdom — mock the whole module
// so that DocumentPdfPreview (now imported by DocumentDetailView) can be loaded.
vi.mock('pdfjs-dist', () => ({
  GlobalWorkerOptions: { workerSrc: '' },
  getDocument: vi.fn(() => ({ promise: new Promise(() => {}), destroy: () => Promise.resolve() })),
}))

// The notes API is exercised only by the note-specific controls; mock it so
// the existing document fetch mock stays focused on the document endpoints.
vi.mock('@/api/notes', () => ({
  updateNote: vi.fn(),
  listNoteVersions: vi.fn(),
  restoreNoteVersion: vi.fn(),
}))

const updateNoteMock = vi.mocked(updateNote)
const listNoteVersionsMock = vi.mocked(listNoteVersions)
const restoreNoteVersionMock = vi.mocked(restoreNoteVersion)

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
    recipient: { id: 5, name: 'John' },
    tags: [{ slug: 'energie', name: 'Energie' }],
    projects: [],
    topics: [],
    document_date: '2026-05-15',
    language: 'nld',
    status: 'indexed',
    mime_type: 'application/pdf',
    page_count: 2,
    created_at: '2026-06-10T12:00:00Z',
    updated_at: '2026-06-11T09:30:00Z',
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
const RECIPIENTS = [
  { id: 5, name: 'John', document_count: 7 },
  { id: 6, name: 'Wife', document_count: 2 },
]
const PROJECTS = [{ slug: 'house-purchase', name: 'House purchase', document_count: 4 }]

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
  /** What POST /api/kinds returns; defaults to echoing the posted name as a
   * created kind. Tests override it to assert dedupe/near-duplicate handling. */
  let createKindResponse: (init?: RequestInit) => Response
  /** What GET /api/documents/12/markdown returns; tests may override. */
  let markdownResponse: () => Response

  beforeEach(async () => {
    detail = makeDetail()
    patchResponse = () => jsonResponse(detail)
    createKindResponse = (init?: RequestInit) => {
      const name = (JSON.parse(String(init?.body ?? '{}')) as { name?: string }).name ?? ''
      const slug = name.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
      return jsonResponse({ slug, name: name.trim() }, 201)
    }
    markdownResponse = () =>
      jsonResponse({ page_count: 1, pages: [{ page_number: 1, markdown: '# Invoice\n\nTotal: €123.45' }] } satisfies DocumentMarkdownResponse)
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
    updateNoteMock.mockReset()
    listNoteVersionsMock.mockReset()
    restoreNoteVersionMock.mockReset()
    fetchMock.mockImplementation((input: unknown, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url === '/api/kinds' && method === 'POST') return Promise.resolve(createKindResponse(init))
      if (url === '/api/kinds') return Promise.resolve(jsonResponse(KINDS))
      if (url === '/api/senders') return Promise.resolve(jsonResponse(SENDERS))
      if (url === '/api/recipients') return Promise.resolve(jsonResponse(RECIPIENTS))
      if (url === '/api/tags') return Promise.resolve(jsonResponse([]))
      if (url === '/api/projects') return Promise.resolve(jsonResponse(PROJECTS))
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
        { path: '/jobs', name: 'jobs', component: Stub },
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

  it('renders rows with values; value-less rows show an em-dash placeholder in read mode', async () => {
    detail = makeDetail({ title: null, summary: null, due_date: null, ocr_confidence: null })
    const w = await mountView()

    // Valued fields render their value.
    expect(rowValue(w, 'kind')).toBe('Invoice')
    expect(rowValue(w, 'sender')).toBe('Eneco')
    expect(rowValue(w, 'document_date')).toBe('15 May 2026')
    expect(rowValue(w, 'language')).toBe('Dutch')
    expect(rowValue(w, 'tags')).toBe('Energie')
    expect(rowValue(w, 'amount')).toBe('123.45 EUR')
    // Value-less fields still render, showing an em-dash, so a field keeps its
    // position when the Edit toggle flips.
    expect(rowValue(w, 'title')).toBe('—')
    expect(rowValue(w, 'due_date')).toBe('—')
    expect(rowValue(w, 'summary')).toBe('—')
    // Read-only system rows still render: status, source, and OCR confidence —
    // which for a born-digital doc (null confidence) reads "Not applicable".
    const text = w.text()
    expect(text).toContain('indexed')
    expect(text).toContain('Upload')
    expect(w.find('[data-testid="ocr-confidence"]').text()).toContain('Not applicable')
    expect(w.find('h1').text()).toBe('Untitled document')
  })

  it('renders the system status as plain text, not a coloured pill', async () => {
    detail = makeDetail({ status: 'indexed' })
    const w = await mountView()
    const status = w.find('[data-testid="status-value"]')
    expect(status.exists()).toBe(true)
    expect(status.element.tagName).toBe('DD')
    expect(status.text()).toBe('indexed')
    // Plain text — no pill (rounded-full / inline-flex / coloured background).
    expect(status.classes()).not.toContain('rounded-full')
    expect(status.classes()).not.toContain('inline-flex')
  })

  it('shows the OCR confidence percentage when a value is present', async () => {
    detail = makeDetail({ ocr_confidence: 91.4 })
    const w = await mountView()
    expect(w.find('[data-testid="ocr-confidence"]').text()).toContain('91%')
  })

  it('labels null OCR confidence on a Paperless import as imported, not born-digital', async () => {
    detail = makeDetail({ ocr_confidence: null, source: 'import' })
    const w = await mountView()
    const ocr = w.find('[data-testid="ocr-confidence"]').text()
    expect(ocr).toContain('Imported (Paperless)')
    expect(ocr).toContain('reused from Paperless')
    expect(ocr).not.toContain('born-digital')
  })

  it('labels null OCR confidence on an upload as born-digital', async () => {
    detail = makeDetail({ ocr_confidence: null, source: 'upload' })
    const w = await mountView()
    const ocr = w.find('[data-testid="ocr-confidence"]').text()
    expect(ocr).toContain('Not applicable')
    expect(ocr).toContain('born-digital')
    expect(ocr).not.toContain('Paperless')
  })

  it('shows no per-field "Change" buttons; an Edit toggle reveals all editors at once', async () => {
    const w = await mountView()
    // No editors and no legacy per-row Change links in read mode.
    expect(w.findAll('.app-link-button')).toHaveLength(0)
    expect(w.find('#edit-title').exists()).toBe(false)

    await w.find('[data-testid="edit-toggle"]').trigger('click')
    await flushPromises()

    // Every field's editor is revealed simultaneously.
    expect(w.find('#edit-title').exists()).toBe(true)
    expect(w.find('#edit-kind').exists()).toBe(true)
    expect(w.find('#edit-sender').exists()).toBe(true)
    expect(w.find('#edit-tags').exists()).toBe(true)
    expect(w.find('#edit-amount').exists()).toBe(true)
    // Read-only values are gone while editing.
    expect(w.find('[data-testid="row-title"] [data-testid="row-value"]').exists()).toBe(false)

    await w.find('[data-testid="edit-toggle"]').trigger('click')
    await flushPromises()
    expect(w.find('#edit-title').exists()).toBe(false)
    expect(w.find('[data-testid="row-title"] [data-testid="row-value"]').exists()).toBe(true)
  })

  it('keeps panels multi-column in edit mode (narrow fields do not span both columns)', async () => {
    const w = await mountView()
    await w.find('[data-testid="edit-toggle"]').trigger('click')
    await flushPromises()
    // kind + language are narrow fields: in edit mode they must keep the
    // two-column grid, not collapse to a single full-width column.
    expect(w.find('[data-testid="row-kind"]').classes()).not.toContain('sm:col-span-2')
    expect(w.find('[data-testid="row-language"]').classes()).not.toContain('sm:col-span-2')
    // Wide fields still span both columns.
    expect(w.find('[data-testid="row-title"]').classes()).toContain('sm:col-span-2')
  })

  it('does not duplicate the field label in edit mode (inline editor label is sr-only)', async () => {
    const w = await mountView()
    await w.find('[data-testid="edit-toggle"]').trigger('click')
    await flushPromises()
    // The <dt> uppercase label remains the single visible label; the editor's
    // own <label> is present for a11y but visually hidden.
    expect(w.find('label[for="edit-kind"]').classes()).toContain('sr-only')
    expect(w.find('label[for="edit-title"]').classes()).toContain('sr-only')
  })

  it('autosaves a text field on change, PATCHing only that field', async () => {
    const w = await mountView()
    patchResponse = () => jsonResponse(makeDetail({ title: 'Nieuwe titel' }))

    await w.find('[data-testid="edit-toggle"]').trigger('click')
    await flushPromises()
    await w.find('#edit-title').setValue('Nieuwe titel')
    await w.find('#edit-title').trigger('change')
    await flushPromises()

    const calls = patchCalls()
    expect(calls).toHaveLength(1)
    expect(calls[0]!.url).toBe('/api/documents/12')
    expect(calls[0]!.body).toEqual({ title: 'Nieuwe titel' }) // exactly one field
    expect(w.find('h1').text()).toBe('Nieuwe titel')
    expect(w.find('[data-testid="saved-title"]').exists()).toBe(true)
  })

  it('does not PATCH a field that was focused but left unchanged', async () => {
    const w = await mountView()
    await w.find('[data-testid="edit-toggle"]').trigger('click')
    await flushPromises()
    await w.find('#edit-title').trigger('change') // no edit
    await flushPromises()
    expect(patchCalls()).toHaveLength(0)
  })

  it('kind editor offers the fetched kind options and autosaves kind_slug', async () => {
    const w = await mountView()
    patchResponse = () =>
      jsonResponse(makeDetail({ kind: { slug: 'receipt', name: 'Receipt' } }))

    await w.find('[data-testid="edit-toggle"]').trigger('click')
    await flushPromises()
    const options = w.find('#edit-kind').findAll('option')
    expect(options.map((option) => option.text())).toEqual([
      'Not set',
      'Invoice',
      'Receipt',
      'Add kind…',
    ])

    await w.find('#edit-kind').setValue('receipt') // <select> setValue emits change
    await flushPromises()

    expect(patchCalls()[0]!.body).toEqual({ kind_slug: 'receipt' })
  })

  it('adds a new kind inline (no blocking prompt), POSTs it, then PATCHes the slug', async () => {
    const w = await mountView()
    patchResponse = () => jsonResponse(makeDetail({ kind: { slug: 'quote', name: 'Quote' } }))

    await w.find('[data-testid="edit-toggle"]').trigger('click')
    await flushPromises()

    // Picking the "Add kind…" sentinel reveals an inline input + confirm.
    await w.find('#edit-kind').setValue('__add_kind__')
    await flushPromises()
    expect(w.find('#kind-add-input').exists()).toBe(true)
    expect(w.find('#edit-kind').exists()).toBe(false)

    await w.find('#kind-add-input').setValue('Quote')
    await w.find('[data-testid="kind-add-confirm"]').trigger('click')
    await flushPromises()

    // The new kind was created on the backend, then the slug was autosaved.
    const postCall = fetchMock.mock.calls.find(
      ([url, init]) => String(url) === '/api/kinds' && (init as RequestInit | undefined)?.method === 'POST',
    )
    expect(postCall).toBeDefined()
    expect(JSON.parse(String((postCall![1] as RequestInit).body))).toEqual({ name: 'Quote' })
    expect(patchCalls()[0]!.body).toEqual({ kind_slug: 'quote' })

    // The inline input collapses back to the select, now holding the new slug.
    expect(w.find('#kind-add-input').exists()).toBe(false)
    expect((w.find('#edit-kind').element as HTMLSelectElement).value).toBe('quote')
  })

  it('keeps the add-kind input open and shows the conflict on a near-duplicate (409)', async () => {
    const w = await mountView()
    createKindResponse = () =>
      jsonResponse(
        {
          detail: "a similar kind named 'Quote' already exists; select it instead",
          existing_slug: 'quote',
          existing_name: 'Quote',
        },
        409,
      )

    await w.find('[data-testid="edit-toggle"]').trigger('click')
    await flushPromises()
    await w.find('#edit-kind').setValue('__add_kind__')
    await flushPromises()
    await w.find('#kind-add-input').setValue('Quotes')
    await w.find('[data-testid="kind-add-confirm"]').trigger('click')
    await flushPromises()

    // No PATCH ran, the input stays open, and the conflict message is shown.
    expect(patchCalls()).toHaveLength(0)
    expect(w.find('#kind-add-input').exists()).toBe(true)
    expect(w.find('#kind-add-input-error').text()).toContain('Quote')
  })

  it('sender editor has a datalist fed by /api/senders', async () => {
    const w = await mountView()
    await w.find('[data-testid="edit-toggle"]').trigger('click')
    await flushPromises()
    expect(w.find('#edit-sender').attributes('list')).toBe('sender-options')
    const options = w.find('datalist#sender-options').findAll('option')
    expect(options.map((option) => option.attributes('value'))).toEqual(['Eneco'])
  })

  it('shows the recipient value in read mode', async () => {
    detail = makeDetail({ recipient: { id: 5, name: 'John' } })
    const w = await mountView()
    expect(rowValue(w, 'recipient')).toBe('John')
  })

  it('recipient editor offers the fetched recipients and autosaves the chosen name', async () => {
    const w = await mountView()
    patchResponse = () => jsonResponse(makeDetail({ recipient: { id: 6, name: 'Wife' } }))

    await w.find('[data-testid="edit-toggle"]').trigger('click')
    await flushPromises()
    const options = w.find('#edit-recipient').findAll('option')
    expect(options.map((option) => option.text())).toEqual([
      'Not set',
      'John',
      'Wife',
      'Add recipient…',
    ])

    await w.find('#edit-recipient').setValue('Wife') // <select> setValue emits change
    await flushPromises()

    expect(patchCalls()[0]!.body).toEqual({ recipient: 'Wife' })
    expect((w.find('#edit-recipient').element as HTMLSelectElement).value).toBe('Wife')
    expect(w.find('[data-testid="saved-recipient"]').exists()).toBe(true)
  })

  it('adds a new recipient inline (no blocking prompt) and PATCHes the typed name', async () => {
    const w = await mountView()
    patchResponse = () => jsonResponse(makeDetail({ recipient: { id: 7, name: 'Landlord' } }))

    await w.find('[data-testid="edit-toggle"]').trigger('click')
    await flushPromises()

    // Picking the "Add recipient…" sentinel reveals an inline input + confirm.
    await w.find('#edit-recipient').setValue('__add_recipient__')
    await flushPromises()
    expect(w.find('#recipient-add-input').exists()).toBe(true)
    expect(w.find('#edit-recipient').exists()).toBe(false)

    await w.find('#recipient-add-input').setValue('Landlord')
    await w.find('[data-testid="recipient-add-confirm"]').trigger('click')
    await flushPromises()

    expect(patchCalls()[0]!.body).toEqual({ recipient: 'Landlord' })
    // The inline input collapses back to the select, now holding the new name.
    expect(w.find('#recipient-add-input').exists()).toBe(false)
    expect((w.find('#edit-recipient').element as HTMLSelectElement).value).toBe('Landlord')
  })

  it('tags editor sends a comma-split full-replacement list', async () => {
    const w = await mountView()
    await w.find('[data-testid="edit-toggle"]').trigger('click')
    await flushPromises()
    await w.find('#edit-tags').setValue(' energie,  wonen ,')
    await w.find('#edit-tags').trigger('change')
    await flushPromises()
    expect(patchCalls()[0]!.body).toEqual({ tags: ['energie', 'wonen'] })
  })

  it('projects editor sends a comma-split full-replacement list', async () => {
    const w = await mountView()
    await w.find('[data-testid="edit-toggle"]').trigger('click')
    await flushPromises()
    await w.find('#edit-projects').setValue(' House purchase,  Taxes ,')
    await w.find('#edit-projects').trigger('change')
    await flushPromises()
    expect(patchCalls()[0]!.body).toEqual({ projects: ['House purchase', 'Taxes'] })
  })

  it('has no topics editor in edit mode (topics are read-only)', async () => {
    detail = makeDetail({ topics: ['thermostat installation', 'boiler maintenance'] })
    const w = await mountView()
    await w.find('[data-testid="edit-toggle"]').trigger('click')
    await flushPromises()
    expect(w.find('#edit-topics').exists()).toBe(false)
  })

  it('read mode renders each topic as a badge', async () => {
    detail = makeDetail({ topics: ['thermostat installation', 'boiler maintenance'] })
    const w = await mountView()
    expect(rowValue(w, 'topics')).toContain('thermostat installation')
    const badges = w.findAll('[data-testid="topic-badge"]')
    expect(badges).toHaveLength(2)
    expect(badges[0]!.text()).toBe('thermostat installation')
    expect(badges[1]!.text()).toBe('boiler maintenance')
  })

  it('hides the topics row in read mode when there are none', async () => {
    detail = makeDetail({ topics: [] })
    const w = await mountView()
    expect(w.find('[data-testid="row-topics"]').exists()).toBe(false)
  })

  it('read mode renders each project as a badge link to the project-filtered dashboard', async () => {
    detail = makeDetail({
      projects: [
        { slug: 'house-purchase', name: 'House purchase' },
        { slug: 'taxes', name: 'Taxes' },
      ],
    })
    const w = await mountView()
    expect(rowValue(w, 'projects')).toContain('House purchase')
    const badges = w.findAll('[data-testid="project-badge"]')
    expect(badges).toHaveLength(2)
    expect(badges[0]!.attributes('href')).toBe('/?project=house-purchase')
    expect(badges[1]!.attributes('href')).toBe('/?project=taxes')
  })

  it('shows the projects row with an em-dash in read mode when there are none', async () => {
    detail = makeDetail({ projects: [] })
    const w = await mountView()
    expect(w.find('[data-testid="row-projects"]').exists()).toBe(true)
    expect(rowValue(w, 'projects')).toBe('—')
  })

  it('shows an inline field error and keeps editing on a 422', async () => {
    const w = await mountView()
    patchResponse = () => jsonResponse({ detail: "unknown kind slug: 'bogus'" }, 422)

    await w.find('[data-testid="edit-toggle"]').trigger('click')
    await flushPromises()
    await w.find('#edit-kind').setValue('receipt')
    await flushPromises()

    expect(w.find('#edit-kind-error').text()).toContain('unknown kind slug')
    expect(w.find('#edit-kind').exists()).toBe(true) // still editing
    expect(w.find('[data-testid="detail-banner"]').exists()).toBe(false)
  })

  it('validates the amount locally and does not PATCH a non-numeric value', async () => {
    const w = await mountView()
    await w.find('[data-testid="edit-toggle"]').trigger('click')
    await flushPromises()
    await w.find('#edit-amount').setValue('not-a-number')
    await w.find('#edit-amount').trigger('change')
    await flushPromises()
    expect(patchCalls()).toHaveLength(0)
    expect(w.find('#edit-amount-error').text()).toContain('number')
  })

  it('autosaves a date once, when focus leaves the day/month/year group', async () => {
    const w = await mountView()
    await w.find('[data-testid="edit-toggle"]').trigger('click')
    await flushPromises()

    // Edit a sub-field; the per-sub-field change must NOT fire a save yet.
    await w.find('#edit-document-date-day').setValue('16')
    await w.find('#edit-document-date-day').trigger('change')
    await flushPromises()
    expect(patchCalls()).toHaveLength(0)

    // Focus leaving the whole fieldset (relatedTarget outside) saves once.
    await w.find('[data-testid="row-document_date"] fieldset').trigger('focusout')
    await flushPromises()

    const calls = patchCalls()
    expect(calls).toHaveLength(1)
    expect(calls[0]!.body).toEqual({ document_date: '2026-05-16' })
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
    // The read-only ingestion date and last-edited timestamp round out the trio.
    expect(stats).toContain('Ingested')
    expect(stats).toContain('10 June 2026') // created_at, formatted
    expect(stats).toContain('Last edited')
    expect(stats).toContain('11 June 2026') // updated_at, formatted
    // A null amount is dropped from the hero entirely — no em-dash placeholder.
    expect(stats).not.toContain('Amount')
    expect(stats).not.toContain('—')
  })

  it('hides value-less hero stats, but renders value-less metadata rows/groups in both modes', async () => {
    // A general document: a kind and a date, but no sender and no amount.
    detail = makeDetail({
      title: 'Brief van de gemeente',
      kind: { slug: 'letter', name: 'Letter' },
      sender: null,
      amount_total: null,
      currency: null,
      tags: [],
    })
    const w = await mountView()

    // Hero shows only the stats that have a value.
    const stats = w.find('[data-testid="hero-stats"]').text()
    expect(stats).toContain('Letter') // kind
    expect(stats).toContain('15 May 2026') // document date
    expect(stats).not.toContain('Sender')
    expect(stats).not.toContain('Amount')

    // The Financial group and the Sender row now render in read mode too (with an
    // em-dash) so the details list keeps the same shape across the Edit toggle.
    expect(w.text()).toContain('Financial')
    expect(w.find('[data-testid="row-amount"]').exists()).toBe(true)
    expect(rowValue(w, 'amount')).toBe('—')
    expect(w.find('[data-testid="row-sender"]').exists()).toBe(true)
    expect(rowValue(w, 'sender')).toBe('—')
    expect(w.find('[data-testid="row-document_date"]').exists()).toBe(true)

    // Edit mode still shows every field and group.
    await w.find('[data-testid="edit-toggle"]').trigger('click')
    await flushPromises()
    expect(w.text()).toContain('Financial')
    expect(w.find('[data-testid="row-amount"]').exists()).toBe(true)
    expect(w.find('[data-testid="row-sender"]').exists()).toBe(true)
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

    // Empty markdown too: with no readable text and no preview, the no-preview
    // panel is the only fallback left.
    markdownResponse = () => jsonResponse({ page_count: 0, pages: [] } satisfies DocumentMarkdownResponse)
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

  it('fetches markdown eagerly on load and renders the document-text reader', async () => {
    const markdownCalls = () =>
      fetchMock.mock.calls.filter((c) => String(c[0]).endsWith('/markdown'))
    const w = await mountView()

    // Fetched once on document load — no disclosure to open.
    expect(markdownCalls()).toHaveLength(1)
    expect(w.find('[data-testid="markdown-content"]').exists()).toBe(true)
    expect(w.find('[data-testid="markdown-content"]').html()).toContain('Invoice')
  })

  it('collapses the document text behind a toggle (mobile reachability fix)', async () => {
    const w = await mountView()
    const toggle = w.find('[data-testid="markdown-toggle"]')
    const bodyStyle = () => w.find('#document-markdown-body').attributes('style') ?? ''

    // jsdom has no matchMedia → defaults to expanded; the body is shown.
    expect(toggle.exists()).toBe(true)
    expect(bodyStyle()).not.toContain('display: none')
    expect(toggle.text()).toBe('Hide')

    // Collapsing hides the body (v-show) but keeps it in the DOM.
    await toggle.trigger('click')
    expect(bodyStyle()).toContain('display: none')
    expect(w.find('[data-testid="markdown-content"]').exists()).toBe(true)
    expect(toggle.text()).toBe('Show')

    // …and it expands again.
    await toggle.trigger('click')
    expect(bodyStyle()).not.toContain('display: none')
    expect(toggle.text()).toBe('Hide')
  })

  it('shows the empty state when page_count is 0', async () => {
    markdownResponse = () => jsonResponse({ page_count: 0, pages: [] } satisfies DocumentMarkdownResponse)
    const w = await mountView()
    expect(w.find('[data-testid="markdown-empty"]').exists()).toBe(true)
    expect(w.find('[data-testid="markdown-content"]').exists()).toBe(false)
  })

  it('shows the document-text reader (not the no-preview fallback) for a text document', async () => {
    detail = makeDetail({ mime_type: 'text/plain', has_searchable_pdf: false })
    markdownResponse = () =>
      jsonResponse({
        page_count: 1,
        pages: [{ page_number: 1, markdown: '# Letter\n\nDear sir' }],
      } satisfies DocumentMarkdownResponse)
    const w = await mountView()
    // The reader is the primary content for a no-PDF text doc.
    expect(w.find('[data-testid="markdown-content"]').exists()).toBe(true)
    expect(w.find('[data-testid="markdown-content"]').html()).toContain('Letter')
    // The no-preview fallback must not appear when there is readable text.
    expect(w.find('[data-testid="preview-fallback"]').exists()).toBe(false)
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

  function detailGetCount(): number {
    return fetchMock.mock.calls.filter(
      (call) => String(call[0]) === '/api/documents/12' && ((call[1] as RequestInit | undefined)?.method ?? 'GET') === 'GET',
    ).length
  }

  it('refetches and refreshes status when the jobs store reports an event for this document', async () => {
    detail = makeDetail({ status: 'ocr' })
    const w = await mountView()
    expect(w.text()).toContain('ocr') // the System-panel status badge

    // The pipeline finishes in the background: the next fetch returns 'indexed'.
    detail = makeDetail({ status: 'indexed' })
    const store = useJobsStore()
    store.handle({ document_id: 12, event: 'status_changed', status: 'indexed', title: null })
    await flushPromises()

    expect(w.text()).toContain('indexed')
  })

  it('ignores jobs-store events for a different document', async () => {
    const w = await mountView()
    const before = detailGetCount()
    const store = useJobsStore()
    store.handle({ document_id: 999, event: 'status_changed', status: 'failed', title: null })
    await flushPromises()
    expect(detailGetCount()).toBe(before) // no refetch for an unrelated document
    expect(w.exists()).toBe(true)
  })

  it('links to the document-filtered jobs history', async () => {
    const w = await mountView()
    const link = w.find('[data-testid="view-job-history"]')
    expect(link.exists()).toBe(true)
    expect(link.attributes('href')).toBe('/jobs?document_id=12')
  })

  describe('note editing and version history', () => {
    function noteDetail(overrides: Partial<DocumentDetail> = {}): DocumentDetail {
      return makeDetail({
        source: 'note',
        mime_type: 'text/markdown',
        has_searchable_pdf: false,
        has_thumbnail: false,
        title: 'My note',
        ...overrides,
      })
    }

    it('shows the note-only controls for a note document', async () => {
      detail = noteDetail()
      const w = await mountView()
      expect(w.find('[data-testid="note-edit-button"]').exists()).toBe(true)
      expect(w.find('[data-testid="note-versions"]').exists()).toBe(true)
    })

    it('hides the note-only controls for a non-note document', async () => {
      detail = makeDetail({ source: 'upload' })
      const w = await mountView()
      expect(w.find('[data-testid="note-edit-button"]').exists()).toBe(false)
      expect(w.find('[data-testid="note-versions"]').exists()).toBe(false)
    })

    it('edits the note title and body, calling updateNote', async () => {
      detail = noteDetail()
      markdownResponse = () =>
        jsonResponse({
          page_count: 1,
          pages: [{ page_number: 1, markdown: 'original body' }],
        } satisfies DocumentMarkdownResponse)
      updateNoteMock.mockResolvedValue(noteDetail({ title: 'Updated note' }))
      const w = await mountView()

      await w.find('[data-testid="note-edit-button"]').trigger('click')
      await flushPromises()
      // The body editor is pre-filled with the current markdown body, and there
      // is no separate title input — the title is the first line of the body.
      expect((w.find('#note-edit-body').element as HTMLTextAreaElement).value).toBe('original body')
      expect(w.find('#note-edit-title').exists()).toBe(false)

      await w.find('#note-edit-body').setValue('Updated note\nupdated body')
      await w.find('[data-testid="note-edit-save"]').trigger('click')
      await flushPromises()

      expect(updateNoteMock).toHaveBeenCalledWith(12, {
        title: 'Updated note',
        body_markdown: 'Updated note\nupdated body',
      })
      expect(w.find('h1').text()).toBe('Updated note')
    })

    it('toggles the editor view mode, showing/hiding the editor and preview panes', async () => {
      localStorage.clear() // default mode is 'split'
      detail = noteDetail()
      markdownResponse = () =>
        jsonResponse({
          page_count: 1,
          pages: [{ page_number: 1, markdown: '# Heading\n\nbody' }],
        } satisfies DocumentMarkdownResponse)
      const w = await mountView()

      await w.find('[data-testid="note-edit-button"]').trigger('click')
      await flushPromises()

      // Split (default): both panes present, preview reflects the draft body.
      expect(w.find('[data-testid="note-edit-editor-pane"]').exists()).toBe(true)
      expect(w.find('[data-testid="note-edit-preview-pane"]').exists()).toBe(true)
      expect(w.find('[data-testid="note-edit-preview"]').html()).toContain('<h1')

      // Edit-only: textarea shown, preview hidden.
      await w.get('[data-testid="note-edit-mode-edit"]').trigger('click')
      expect(w.find('[data-testid="note-edit-editor-pane"]').exists()).toBe(true)
      expect(w.find('[data-testid="note-edit-preview-pane"]').exists()).toBe(false)

      // Preview-only: preview shown, textarea hidden.
      await w.get('[data-testid="note-edit-mode-preview"]').trigger('click')
      expect(w.find('[data-testid="note-edit-editor-pane"]').exists()).toBe(false)
      expect(w.find('[data-testid="note-edit-preview-pane"]').exists()).toBe(true)

      // Back to split: both panes again.
      await w.get('[data-testid="note-edit-mode-split"]').trigger('click')
      expect(w.find('[data-testid="note-edit-editor-pane"]').exists()).toBe(true)
      expect(w.find('[data-testid="note-edit-preview-pane"]').exists()).toBe(true)
    })

    it('reflects live edits to the draft body in the preview pane', async () => {
      localStorage.clear()
      detail = noteDetail()
      const w = await mountView()

      await w.find('[data-testid="note-edit-button"]').trigger('click')
      await flushPromises()
      await w.find('#note-edit-body').setValue('# Live heading\n\nedited')
      await flushPromises()

      const preview = w.find('[data-testid="note-edit-preview"]')
      expect(preview.html()).toContain('<h1')
      expect(preview.text()).toContain('Live heading')
    })

    it('opens the version history, calling listNoteVersions', async () => {
      detail = noteDetail()
      listNoteVersionsMock.mockResolvedValue([
        { version_no: 2, title: 'v2', body: 'b2', created_at: '2026-06-02T00:00:00Z' },
        { version_no: 1, title: 'v1', body: 'b1', created_at: '2026-06-01T00:00:00Z' },
      ])
      const w = await mountView()

      await w.find('[data-testid="note-versions-toggle"]').trigger('click')
      await flushPromises()

      expect(listNoteVersionsMock).toHaveBeenCalledWith(12)
      expect(w.find('[data-testid="note-restore-2"]').exists()).toBe(true)
      expect(w.find('[data-testid="note-restore-1"]').exists()).toBe(true)
    })

    it('restores a version, calling restoreNoteVersion', async () => {
      detail = noteDetail()
      listNoteVersionsMock.mockResolvedValue([
        { version_no: 1, title: 'v1', body: 'b1', created_at: '2026-06-01T00:00:00Z' },
      ])
      restoreNoteVersionMock.mockResolvedValue(noteDetail({ title: 'Restored note' }))
      const w = await mountView()

      await w.find('[data-testid="note-versions-toggle"]').trigger('click')
      await flushPromises()
      await w.find('[data-testid="note-restore-1"]').trigger('click')
      await flushPromises()

      expect(restoreNoteVersionMock).toHaveBeenCalledWith(12, 1)
      expect(w.find('h1').text()).toBe('Restored note')
    })
  })
})
