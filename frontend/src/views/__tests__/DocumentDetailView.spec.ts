import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import type Sortable from 'sortablejs'
import DocumentDetailView from '../DocumentDetailView.vue'
import type { DocumentDetail, DocumentMarkdownResponse } from '@/api/documents'
import { listNoteVersions, restoreNoteVersion, updateNote } from '@/api/notes'
import { useJobsStore } from '@/stores/jobs'
import { useReviewQueueStore } from '@/stores/reviewQueue'
import { useDocumentLayout, DEFAULT_CARD_COLUMNS } from '@/composables/useDocumentLayout'
import { useMetadataEditMode } from '@/composables/useMetadataEditMode'

// pdfjs-dist can't run its worker/canvas in jsdom — mock the whole module
// so that DocumentPdfPreview (now imported by DocumentDetailView) can be loaded.
vi.mock('pdfjs-dist', () => ({
  GlobalWorkerOptions: { workerSrc: '' },
  getDocument: vi.fn(() => ({ promise: new Promise(() => {}), destroy: () => Promise.resolve() })),
}))

// sortablejs manipulates real DOM nodes on drag; in unit tests we exercise the
// reactive rendering + wiring (not the drag physics), so stub it out — but
// capture each Sortable.create(el, options) call so a test can invoke the
// component's real onEnd handler with a synthetic drag event (see
// `cardColumnOnEnd` below). `vi.hoisted` is required because `vi.mock` factories
// are hoisted above the top-level imports/consts they close over.
const { capturedSortables } = vi.hoisted(() => ({
  capturedSortables: [] as { el: HTMLElement; options: Sortable.Options }[],
}))
vi.mock('sortablejs', () => ({
  default: {
    create: vi.fn((el: HTMLElement, options: Sortable.Options) => {
      capturedSortables.push({ el, options })
      return { destroy: vi.fn() }
    }),
  },
}))

/** The shared `onEnd` handler wired to both section-card columns (they use one
 * function reference, so either captured column Sortable exposes it). Throws
 * if edit mode was never turned on (no Sortable instances were created). */
function cardColumnOnEnd(): NonNullable<Sortable.Options['onEnd']> {
  const found = capturedSortables.find(
    (s) => s.el.dataset.col === 'left' || s.el.dataset.col === 'right',
  )
  if (!found?.options.onEnd) {
    throw new Error('no section-card Sortable was captured — is edit mode on?')
  }
  return found.options.onEnd
}

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
    deleted_at: null,
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
    review_findings: [],
    user_edited_fields: [],
    events: [],
    comments: [],
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

/** Minimal IntersectionObserver stand-in: jsdom has none. Captures the
 * callback and observed element per instance so a test can synthesize an
 * intersection change via `trigger(isIntersecting)`. */
class FakeIntersectionObserver implements IntersectionObserver {
  static instances: FakeIntersectionObserver[] = []
  readonly root: Element | Document | null = null
  readonly rootMargin = ''
  readonly scrollMargin = ''
  readonly thresholds: ReadonlyArray<number> = []
  private callback: IntersectionObserverCallback
  private observed: Element[] = []

  constructor(callback: IntersectionObserverCallback) {
    this.callback = callback
    FakeIntersectionObserver.instances.push(this)
  }

  observe(target: Element): void {
    this.observed.push(target)
  }

  unobserve(target: Element): void {
    this.observed = this.observed.filter((el) => el !== target)
  }

  disconnect(): void {
    this.observed = []
  }

  takeRecords(): IntersectionObserverEntry[] {
    return []
  }

  /** Synthesize the observer reporting a new intersection state for the
   * (single) observed hero element. */
  trigger(isIntersecting: boolean): void {
    const entry = { isIntersecting, target: this.observed[0] } as IntersectionObserverEntry
    this.callback([entry], this)
  }
}

/** The most recently constructed fake observer — the view creates exactly one. */
function lastIntersectionObserver(): FakeIntersectionObserver {
  const instance = FakeIntersectionObserver.instances.at(-1)
  if (!instance) throw new Error('no IntersectionObserver was constructed')
  return instance
}

// jsdom lacks HTMLDialogElement.showModal/close (ConfirmDialog uses <dialog>).
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
    // useDocumentLayout is a module singleton — reset its persisted state and
    // leave edit mode so hero/card customisation tests don't leak into others.
    const layout = useDocumentLayout()
    layout.resetLayout()
    layout.setEditMode(false)
    // useMetadataEditMode is a module singleton too — reset it so a metadata
    // edit-mode toggle in one test never leaks into the next.
    useMetadataEditMode().setEditMode(false)
    detail = makeDetail()
    patchResponse = () => jsonResponse(detail)
    createKindResponse = (init?: RequestInit) => {
      const name = (JSON.parse(String(init?.body ?? '{}')) as { name?: string }).name ?? ''
      const slug = name.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
      return jsonResponse({ slug, name: name.trim() }, 201)
    }
    markdownResponse = () =>
      jsonResponse({ page_count: 1, pages: [{ page_number: 1, markdown: '# Invoice\n\nTotal: €123.45' }] } satisfies DocumentMarkdownResponse)
    capturedSortables.length = 0
    FakeIntersectionObserver.instances = []
    vi.stubGlobal('IntersectionObserver', FakeIntersectionObserver)
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
      // The detail fetch carries ?include_deleted=true (so trashed docs open
      // read-only), so match on the path, not the exact query string.
      const path = url.split('?')[0]
      if (url === '/api/documents/12/permanent' && method === 'DELETE') {
        return Promise.resolve(new Response(null, { status: 204 }))
      }
      if (url === '/api/documents/12/restore' && method === 'POST') {
        return Promise.resolve(jsonResponse({ ...detail, deleted_at: null }))
      }
      if (path === '/api/documents/12' && method === 'GET') {
        return Promise.resolve(jsonResponse(detail))
      }
      if (path === '/api/documents/12' && method === 'PATCH') {
        return Promise.resolve(patchResponse())
      }
      if (url === '/api/documents/12/verify' && method === 'POST') {
        return Promise.resolve(jsonResponse({ ...detail, review_status: 'verified' }))
      }
      if (path === '/api/documents/12/markdown' && method === 'GET') {
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
        { path: '/deleted', name: 'documents-deleted', component: Stub },
        { path: '/jobs', name: 'jobs', component: Stub },
        { path: '/ask', name: 'ask', component: Stub },
      ],
    })
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = undefined
    vi.unstubAllGlobals()
    // Restore any vi.spyOn (e.g. the disconnect spy below) so a later test's
    // fresh spy starts with empty call history instead of accumulating.
    vi.restoreAllMocks()
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

  it('aligns the Amount and Currency inputs (both labels hidden, no inline hint)', async () => {
    // Regression guard for the financial-panel misalignment: Currency used to
    // render a visible <label> + a hint above its input, pushing it ~2 lines
    // below Amount. Both editors must defer their label to the outer <dt> via
    // hide-label (rendered as sr-only) and carry no header hint, so their inputs
    // share a baseline. JSDOM has no layout, so we assert the DOM that causes the
    // offset rather than pixel positions.
    const w = await mountView()
    await w.find('[data-testid="edit-toggle"]').trigger('click')
    await flushPromises()

    const amountLabel = w.find('label[for="edit-amount"]')
    const currencyLabel = w.find('label[for="edit-currency"]')
    expect(amountLabel.exists()).toBe(true)
    expect(currencyLabel.exists()).toBe(true)
    // Both labels are visually hidden -> zero header height above each input.
    expect(amountLabel.classes()).toContain('sr-only')
    expect(currencyLabel.classes()).toContain('sr-only')
    // No hint paragraph pushes the Currency input down.
    expect(w.find('#edit-currency-hint').exists()).toBe(false)
    expect(w.find('#edit-amount-hint').exists()).toBe(false)
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

  it('projects multiselect adds an existing project from the menu', async () => {
    const w = await mountView()
    await w.find('[data-testid="edit-toggle"]').trigger('click')
    await flushPromises()
    await w.find('#edit-projects').trigger('focus')
    // The existing project (from GET /api/projects) is offered in the menu.
    const option = w.find('[data-testid="edit-projects-option"]')
    expect(option.text()).toBe('House purchase')
    await option.trigger('mousedown')
    await flushPromises()
    expect(patchCalls()[0]!.body).toEqual({ projects: ['House purchase'] })
  })

  it('projects multiselect creates a new project inline via Enter', async () => {
    const w = await mountView()
    await w.find('[data-testid="edit-toggle"]').trigger('click')
    await flushPromises()
    await w.find('#edit-projects').setValue('Taxes')
    await w.find('#edit-projects').trigger('focus')
    // A name that matches no existing option offers a "Create" affordance.
    expect(w.find('[data-testid="edit-projects-create"]').exists()).toBe(true)
    await w.find('#edit-projects').trigger('keydown.enter')
    await flushPromises()
    expect(patchCalls()[0]!.body).toEqual({ projects: ['Taxes'] })
  })

  it('projects multiselect removes a project and PATCHes the reduced list', async () => {
    detail = makeDetail({ projects: [{ slug: 'taxes', name: 'Taxes' }] })
    const w = await mountView()
    await w.find('[data-testid="edit-toggle"]').trigger('click')
    await flushPromises()
    await w.find('[data-testid="edit-projects-remove"]').trigger('click')
    await flushPromises()
    expect(patchCalls()[0]!.body).toEqual({ projects: [] })
  })

  it('treats a project name containing a comma as a single project', async () => {
    // Project names are free text (may contain commas), so the draft must not
    // round-trip through a comma-joined string.
    detail = makeDetail({ projects: [{ slug: 'smith-jones', name: 'Smith, Jones' }] })
    const w = await mountView()
    await w.find('[data-testid="edit-toggle"]').trigger('click')
    await flushPromises()
    // One chip, not two — the comma is part of the name, not a separator.
    expect(w.findAll('[data-testid="edit-projects-chip"]')).toHaveLength(1)
    // Adding another project PATCHes both names intact (the comma name unsplit).
    await w.find('#edit-projects').setValue('Taxes')
    await w.find('#edit-projects').trigger('keydown.enter')
    await flushPromises()
    expect(patchCalls()[0]!.body).toEqual({ projects: ['Smith, Jones', 'Taxes'] })
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
    // The read-only added date and last-edited timestamp round out the trio.
    expect(stats).toContain('Added date')
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

  it('does not refetch the document from a comment change while a re-extraction poll is in flight', async () => {
    // A comment mutation while re-extraction is polling would race the poll's
    // own getDocument call and could overwrite fresh data with stale — the
    // comments card's `changed` handler (reloadDocument) must defer to the
    // in-flight poll, exactly like the SSE watcher does.
    const originalImpl = fetchMock.getMockImplementation()!
    fetchMock.mockImplementation((input: unknown, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url === '/api/documents/12/comments' && method === 'POST') {
        return Promise.resolve(
          jsonResponse({ id: 99, body: 'hi', created_at: '2026-06-10T13:00:00Z' }, 201),
        )
      }
      return originalImpl(input, init)
    })

    // A very long poll interval keeps `extracting` true for the rest of the
    // test without needing fake timers.
    const w = await mountView('/documents/12', { pollIntervalMs: 100_000, extractTimeoutMs: 500_000 })

    await w.find('[data-testid="rerun-extraction"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="detail-banner"]').text()).toContain('Extraction queued')

    const getDocumentCalls = () =>
      fetchMock.mock.calls.filter(
        (call) => String(call[0]) === '/api/documents/12' && (call[1] as RequestInit | undefined)?.method === undefined,
      ).length
    const callsBefore = getDocumentCalls()

    await w.find('[data-testid="comment-add-body"]').setValue('hi')
    await w.find('[data-testid="comment-add-submit"]').trigger('click')
    await flushPromises()

    // The comment mutation happened...
    expect(
      fetchMock.mock.calls.some(
        (call) => String(call[0]) === '/api/documents/12/comments' && (call[1] as RequestInit)?.method === 'POST',
      ),
    ).toBe(true)
    // ...but the guarded reloadDocument did not issue another GET while extracting.
    expect(getDocumentCalls()).toBe(callsBefore)
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

  it('surfaces a field-mapped finding (implausible date) in the top panel too', async () => {
    detail = makeDetail({
      review_status: 'needs_review',
      validation: {
        findings: [
          {
            rule: 'date_plausibility',
            field: 'document_date',
            severity: 'warn',
            message: 'document_date is in the future',
          },
        ],
      },
    })
    const w = await mountView()
    const banner = w.find('[data-testid="validation-findings"]')
    expect(banner.exists()).toBe(true)
    // Plain-language title + the underlying reason, both in the top panel — no
    // longer hidden behind only a per-field ⚠ badge.
    expect(banner.text()).toContain('Unlikely date')
    expect(banner.text()).toContain('document_date is in the future')
    // The per-field badge still appears beside the date row (secondary signal).
    expect(w.findAll('[data-testid="validation-badge"]').length).toBeGreaterThan(0)
  })

  it('hides the why-panel once the document is no longer needs_review', async () => {
    detail = makeDetail({
      review_status: 'verified',
      validation: {
        findings: [
          { rule: 'date_plausibility', field: 'document_date', severity: 'warn', message: 'x' },
        ],
      },
    })
    const w = await mountView()
    expect(w.find('[data-testid="validation-findings"]').exists()).toBe(false)
  })

  // --- Review queue (queue mode) ---------------------------------------------

  function seedQueue(ids: number[], index = 0): ReturnType<typeof useReviewQueueStore> {
    const q = useReviewQueueStore()
    q.ids = ids
    q.index = index
    return q
  }

  it('shows the queue bar with position and controls in queue mode', async () => {
    seedQueue([12, 13])
    detail = makeDetail({ review_status: 'needs_review' })
    const w = await mountView('/documents/12?queue=1')

    expect(w.find('[data-testid="review-queue-bar"]').exists()).toBe(true)
    expect(w.find('[data-testid="review-queue-position"]').text()).toContain('1 of 2')
    // First document: Prev disabled; Verify & Next available (not yet verified).
    expect(w.find('[data-testid="queue-prev"]').attributes('disabled')).toBeDefined()
    expect(w.find('[data-testid="queue-verify-next"]').exists()).toBe(true)
    expect(w.find('[data-testid="queue-next"]').exists()).toBe(true)
  })

  it('is not in queue mode without ?queue=1', async () => {
    seedQueue([12, 13])
    detail = makeDetail({ review_status: 'needs_review' })
    const w = await mountView('/documents/12')
    expect(w.find('[data-testid="review-queue-bar"]').exists()).toBe(false)
  })

  it('Next advances the cursor and navigates to the next queued document', async () => {
    const q = seedQueue([12, 13])
    detail = makeDetail({ review_status: 'needs_review' }) // still flagged -> keep in queue
    const w = await mountView('/documents/12?queue=1')
    const push = vi.spyOn(router, 'push').mockResolvedValue()

    await w.find('[data-testid="queue-next"]').trigger('click')

    expect(q.index).toBe(1) // stepped, not dropped (still needs_review)
    expect(q.total).toBe(2)
    expect(push).toHaveBeenCalledWith({
      name: 'document-detail',
      params: { id: 13 },
      query: { queue: '1' },
    })
  })

  it('Verify & next marks verified, drops the doc from the queue, and advances', async () => {
    const q = seedQueue([12, 13])
    detail = makeDetail({ review_status: 'needs_review' })
    const w = await mountView('/documents/12?queue=1')
    const push = vi.spyOn(router, 'push').mockResolvedValue()

    await w.find('[data-testid="queue-verify-next"]').trigger('click')
    await flushPromises()

    expect(q.total).toBe(1) // #12 verified -> removed
    expect(q.ids).toEqual([13])
    expect(push).toHaveBeenCalledWith({
      name: 'document-detail',
      params: { id: 13 },
      query: { queue: '1' },
    })
  })

  it('Exit clears the queue and returns to the dashboard', async () => {
    const q = seedQueue([12, 13])
    detail = makeDetail({ review_status: 'needs_review' })
    const w = await mountView('/documents/12?queue=1')
    const push = vi.spyOn(router, 'push').mockResolvedValue()

    await w.find('[data-testid="queue-exit"]').trigger('click')

    expect(q.isActive).toBe(false)
    expect(push).toHaveBeenCalledWith('/')
  })

  it('finishing the last queued document exits to the dashboard', async () => {
    const q = seedQueue([12]) // only one left
    detail = makeDetail({ review_status: 'verified' }) // already resolved -> Next drops it
    const w = await mountView('/documents/12?queue=1')
    const push = vi.spyOn(router, 'push').mockResolvedValue()

    await w.find('[data-testid="queue-next"]').trigger('click')

    expect(q.isActive).toBe(false)
    expect(push).toHaveBeenCalledWith('/')
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
      if (url.split('?')[0] === '/api/documents/12' && method === 'GET')
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

  it('links "Ask about this document" to /ask in a new tab, seeding the title (W1)', async () => {
    const w = await mountView()
    const button = w.find('[data-testid="ask-about-document"]')
    expect(button.exists()).toBe(true)
    expect(button.attributes('target')).toBe('_blank')
    // Guard against tab-napping regressing.
    expect(button.attributes('rel')).toContain('noopener')
    const href = button.attributes('href') ?? ''
    const url = new URL(href, 'http://localhost')
    expect(url.pathname).toBe('/ask')
    expect(url.searchParams.get('q') ?? '').toContain('Energierekening mei 2026')
  })

  it('places the "Ask about this document" button in the hero, not the Actions panel', async () => {
    const w = await mountView()
    const hero = w.find('#document-hero')
    expect(hero.find('[data-testid="ask-about-document"]').exists()).toBe(true)
    // Exactly one — it was moved out of the Actions panel, not duplicated.
    expect(w.findAll('[data-testid="ask-about-document"]')).toHaveLength(1)
  })

  it('omits the parenthetical entirely when kind/sender/date are all missing (W1)', async () => {
    detail = makeDetail({ kind: null, sender: null, document_date: null })
    const w = await mountView()
    const href = w.find('[data-testid="ask-about-document"]').attributes('href') ?? ''
    const q = new URL(href, 'http://localhost').searchParams.get('q') ?? ''
    // No empty parenthetical and no dangling open-paren at all.
    expect(q).not.toContain('()')
    expect(q).not.toContain('(')
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

  // --- Layout customisation: hero fields (W5) + section cards (W6) ------------

  describe('layout customisation (Edit layout)', () => {
    /** Ordered hero-field testids currently in the hero (read or edit mode). */
    function heroFieldOrder(w: VueWrapper): string[] {
      return w
        .find('#document-hero')
        .findAll('[data-testid^="hero-field-"]')
        .map((el) => el.attributes('data-testid') ?? '')
        .filter((id) => !id.startsWith('hero-field-toggle-'))
    }

    it('renders the recipient field in the hero when present (added in W5)', async () => {
      detail = makeDetail({ recipient: { id: 5, name: 'John' } })
      const w = await mountView()
      const field = w.find('#document-hero [data-testid="hero-field-recipient"]')
      expect(field.exists()).toBe(true)
      expect(field.text()).toContain('John')
    })

    it('omits a hero field hidden in the saved layout (read mode)', async () => {
      useDocumentLayout().setHeroFieldVisible('sender', false)
      const w = await mountView()
      expect(w.find('#document-hero [data-testid="hero-field-sender"]').exists()).toBe(false)
      // Other visible fields still render.
      expect(w.find('#document-hero [data-testid="hero-field-kind"]').exists()).toBe(true)
    })

    it('drops a visible-but-empty field from the read-mode hero', async () => {
      // due_date is hidden by default; make it visible but leave it null.
      useDocumentLayout().setHeroFieldVisible('due_date', true)
      detail = makeDetail({ due_date: null })
      const w = await mountView()
      expect(w.find('#document-hero [data-testid="hero-field-due_date"]').exists()).toBe(false)
    })

    it('renders hero fields in the saved order', async () => {
      useDocumentLayout().setHeroFieldOrder(['sender', 'kind'])
      const w = await mountView()
      const order = heroFieldOrder(w)
      expect(order.indexOf('hero-field-sender')).toBeLessThan(order.indexOf('hero-field-kind'))
    })

    it('shows no edit-only controls when edit mode is off', async () => {
      const w = await mountView()
      expect(w.find('[data-testid="hero-fields-editor"]').exists()).toBe(false)
      expect(w.findAll('[data-testid^="hero-field-toggle-"]')).toHaveLength(0)
      expect(w.findAll('[data-testid^="card-drag-handle-"]')).toHaveLength(0)
      expect(w.find('[data-testid="reset-layout"]').exists()).toBe(false)
      expect(w.find('[data-testid="edit-layout-toggle"]').text()).toBe('Edit layout')
    })

    it('reveals hero field toggles and card drag handles when edit mode is on', async () => {
      const w = await mountView()
      await w.find('[data-testid="edit-layout-toggle"]').trigger('click')
      await flushPromises()

      // Every known field is listed for toggling — including ones hidden by
      // default (language) and ones with no value (due_date).
      expect(w.find('[data-testid="hero-field-toggle-kind"]').exists()).toBe(true)
      expect(w.find('[data-testid="hero-field-toggle-recipient"]').exists()).toBe(true)
      expect(w.find('[data-testid="hero-field-toggle-language"]').exists()).toBe(true)
      // Section cards expose a drag handle in both columns.
      expect(w.find('[data-testid="card-drag-handle-preview"]').exists()).toBe(true)
      expect(w.find('[data-testid="card-drag-handle-metadata"]').exists()).toBe(true)
      // Reset appears only in edit mode, and the toggle now reads "Done".
      expect(w.find('[data-testid="reset-layout"]').exists()).toBe(true)
      expect(w.find('[data-testid="edit-layout-toggle"]').text()).toBe('Done')
    })

    it('hiding a field via its toggle persists and removes it from the read-mode hero', async () => {
      const w = await mountView()
      await w.find('[data-testid="edit-layout-toggle"]').trigger('click')
      await flushPromises()

      await w.find('[data-testid="hero-field-toggle-kind"]').setValue(false)
      await w.find('[data-testid="edit-layout-toggle"]').trigger('click') // back to read
      await flushPromises()

      expect(w.find('#document-hero [data-testid="hero-field-kind"]').exists()).toBe(false)
      expect(
        useDocumentLayout().heroFields.value.find((f) => f.key === 'kind')?.visible,
      ).toBe(false)
    })

    it('renders section cards in the saved order within a column', async () => {
      useDocumentLayout().setColumn('right', ['markdown', 'preview', 'series-chart'])
      const w = await mountView()
      const ids = w
        .find('#document-preview-column')
        .findAll('[data-testid^="section-card-"]')
        .map((el) => el.attributes('data-testid') ?? '')
      expect(ids.indexOf('section-card-markdown')).toBeLessThan(ids.indexOf('section-card-preview'))
    })

    /** A DocumentSeriesTrend stand-in that reports its presence on mount, the
     * same way the real component does once its `load()` resolves. */
    function makeSeriesTrendStub(presence: boolean): Record<string, unknown> {
      return {
        props: ['documentId'],
        emits: ['presence'],
        template: '<div data-testid="series-trend-stub" />',
        mounted(this: { $emit: (event: string, value: boolean) => void }) {
          this.$emit('presence', presence)
        },
      }
    }

    it('hides the series-chart card, even in edit mode, once DocumentSeriesTrend reports no series', async () => {
      // Regression test: the wrapper's `empty:hidden` can't hide the card in
      // edit mode because the drag handle keeps it non-empty — cardPresent
      // must gate it instead.
      await router.push('/documents/12')
      wrapper = mount(DocumentDetailView, {
        global: {
          plugins: [router, pinia],
          stubs: { DocumentSeriesTrend: makeSeriesTrendStub(false) },
        },
      })
      await flushPromises()
      await wrapper.find('[data-testid="edit-layout-toggle"]').trigger('click')
      await flushPromises()

      expect(wrapper.find('[data-testid="section-card-series-chart"]').exists()).toBe(false)
      // Unaffected sibling cards still render normally.
      expect(wrapper.find('[data-testid="card-drag-handle-preview"]').exists()).toBe(true)
    })

    it('shows the series-chart card in edit mode once DocumentSeriesTrend reports a present series', async () => {
      await router.push('/documents/12')
      wrapper = mount(DocumentDetailView, {
        global: {
          plugins: [router, pinia],
          stubs: { DocumentSeriesTrend: makeSeriesTrendStub(true) },
        },
      })
      await flushPromises()
      await wrapper.find('[data-testid="edit-layout-toggle"]').trigger('click')
      await flushPromises()

      expect(wrapper.find('[data-testid="section-card-series-chart"]').exists()).toBe(true)
      expect(wrapper.find('[data-testid="card-drag-handle-series-chart"]').exists()).toBe(true)
    })

    it('reorders the metadata column independently of the preview column', async () => {
      useDocumentLayout().setColumn('left', ['notes', 'history', 'metadata', 'comments', 'actions'])
      const w = await mountView()
      const ids = w
        .find('#document-metadata-column')
        .findAll('[data-testid^="section-card-"]')
        .map((el) => el.attributes('data-testid') ?? '')
      // notes is absent (not a note doc), so the metadata column is history,
      // metadata, comments, actions in that saved order.
      expect(ids).toEqual([
        'section-card-history',
        'section-card-metadata',
        'section-card-comments',
        'section-card-actions',
      ])
    })

    it('moves a card across columns via the shared-group onEnd handler', async () => {
      // A note document so 'notes' renders (present === full for both
      // columns) — isolates the cross-column move itself from the separate
      // present/full index-mapping concern (covered below).
      detail = makeDetail({ source: 'note' })
      const layout = useDocumentLayout()
      layout.resetLayout()
      const w = await mountView()
      await w.find('[data-testid="edit-layout-toggle"]').trigger('click')
      await flushPromises()

      // Both column Sortables share one onEnd (group: 'doc-cards'); grab it
      // from whichever card-column Sortable.create call was captured.
      const onEnd = cardColumnOnEnd()
      // Simulate SortableJS dropping "comments" (left index 2 of the default
      // left column: notes, metadata, comments, actions, history) into the
      // right column at index 0.
      const evt = {
        from: { dataset: { col: 'left' }, insertBefore: vi.fn(), children: [] },
        to: { dataset: { col: 'right' } },
        item: {},
        oldIndex: 2,
        newIndex: 0,
      } as unknown as Sortable.SortableEvent
      onEnd(evt)
      await flushPromises()

      expect(layout.cardColumns.value.right[0]).toBe('comments')
      expect(layout.cardColumns.value.left).not.toContain('comments')
      // The manual DOM-revert ran (insertBefore is called with the dragged
      // item back at its original position in the source list).
      expect((evt.from as unknown as { insertBefore: ReturnType<typeof vi.fn> }).insertBefore).toHaveBeenCalled()
    })

    it('renders a card body in whichever column holds it (cross-column render)', async () => {
      // Regression: the two columns must share one card renderer. A card
      // dragged into the *other* column has to render its body there — not
      // vanish because that column's template lacked a branch for its id.
      const layout = useDocumentLayout()
      layout.resetLayout()
      layout.moveCard('comments', 'right', 0) // comments now lives in the right column
      const w = await mountView()
      // The comments card body renders inside the RIGHT (preview) column…
      expect(
        w.find('#document-preview-column [data-testid="document-comments"]').exists(),
      ).toBe(true)
      // …and no longer in the left column it used to be pinned to.
      expect(
        w.find('#document-metadata-column [data-testid="document-comments"]').exists(),
      ).toBe(false)
    })

    it('does not render an empty preview card for a text-only note', async () => {
      // Regression: a note has no image/PDF preview and its original is text,
      // so the preview card would render an empty `.card` (a stray thin line)
      // and, in edit mode, a drag handle detached from any visible panel.
      detail = makeDetail({ source: 'note', mime_type: 'text/markdown', has_searchable_pdf: false })
      const w = await mountView()
      expect(w.find('[data-testid="section-card-preview"]').exists()).toBe(false)
      expect(w.find('#document-preview-card').exists()).toBe(false)

      // …and no stray drag handle for it in edit mode.
      await w.find('[data-testid="edit-layout-toggle"]').trigger('click')
      await flushPromises()
      expect(w.find('[data-testid="card-drag-handle-preview"]').exists()).toBe(false)
    })

    it('maps a rendered-index drop target to the correct full-column index around a hidden card', async () => {
      // Default document is NOT a note, so 'notes' (always first in the
      // default left column) renders no drag handle and is absent from
      // SortableJS's rendered list. evt.newIndex is relative to that rendered
      // list, so dropping at rendered index 0 of the left column must land
      // the moved card right after the hidden 'notes' in the FULL column
      // array — not literally at full index 0 (which would incorrectly sit
      // ahead of 'notes').
      const layout = useDocumentLayout()
      layout.resetLayout()
      const w = await mountView() // detail defaults to source: 'upload' (not a note)
      await w.find('[data-testid="edit-layout-toggle"]').trigger('click')
      await flushPromises()

      const onEnd = cardColumnOnEnd()
      const evt = {
        from: { dataset: { col: 'right' }, insertBefore: vi.fn(), children: [] },
        to: { dataset: { col: 'left' } },
        item: {},
        oldIndex: 0, // 'preview' — first rendered card in the right column
        newIndex: 0, // top of the *rendered* left column (before 'metadata', since 'notes' is hidden)
      } as unknown as Sortable.SortableEvent
      onEnd(evt)
      await flushPromises()

      expect(layout.cardColumns.value.left[0]).toBe('notes')
      expect(layout.cardColumns.value.left[1]).toBe('preview')
      expect(layout.cardColumns.value.left[2]).toBe('metadata')
      expect(layout.cardColumns.value.right).not.toContain('preview')
    })

    it('reset layout restores the default hero order and card order', async () => {
      const layout = useDocumentLayout()
      layout.setHeroFieldOrder(['amount', 'kind'])
      layout.setColumn('left', [...DEFAULT_CARD_COLUMNS.left].reverse())
      layout.setColumn('right', [...DEFAULT_CARD_COLUMNS.right].reverse())
      const w = await mountView()
      await w.find('[data-testid="edit-layout-toggle"]').trigger('click')
      await flushPromises()
      await w.find('[data-testid="reset-layout"]').trigger('click')
      await flushPromises()

      expect(layout.heroFields.value[0]!.key).toBe('kind')
      expect(layout.cardColumns.value).toEqual(DEFAULT_CARD_COLUMNS)
    })

    it('resets edit mode when the view unmounts so it never persists across navigation', async () => {
      const layout = useDocumentLayout()
      const w = await mountView()
      await w.find('[data-testid="edit-layout-toggle"]').trigger('click')
      await flushPromises()
      expect(layout.editMode.value).toBe(true)

      // Leaving the view (SPA navigation) must clear the singleton flag, otherwise
      // returning would show edit affordances with no Sortable instances attached.
      w.unmount()
      expect(layout.editMode.value).toBe(false)
    })

    it('resets metadata edit mode when in-view queue navigation changes route.params.id without unmounting', async () => {
      // Prev/Next in the review queue changes route.params.id while the same
      // DocumentDetailView instance stays mounted (App.vue's RouterView is
      // unkeyed). If the module-singleton editMode flag survived that
      // navigation, the metadata editor's non-immediate hydration watcher
      // would never re-fire for the new document, leaving blank edit inputs.
      const detailB = makeDetail({ id: 13, title: 'Doc B' })
      fetchMock.mockImplementation((input: unknown, init?: RequestInit) => {
        const url = String(input)
        const method = init?.method ?? 'GET'
        if (url === '/api/kinds' && method === 'POST') return Promise.resolve(createKindResponse(init))
        if (url === '/api/kinds') return Promise.resolve(jsonResponse(KINDS))
        if (url === '/api/senders') return Promise.resolve(jsonResponse(SENDERS))
        if (url === '/api/recipients') return Promise.resolve(jsonResponse(RECIPIENTS))
        if (url === '/api/tags') return Promise.resolve(jsonResponse([]))
        if (url === '/api/projects') return Promise.resolve(jsonResponse(PROJECTS))
        if (url.split('?')[0] === '/api/documents/12' && method === 'GET')
          return Promise.resolve(jsonResponse(detail))
        if (url.split('?')[0] === '/api/documents/13' && method === 'GET')
          return Promise.resolve(jsonResponse(detailB))
        if (url === '/api/documents/12/markdown' && method === 'GET') return Promise.resolve(markdownResponse())
        if (url === '/api/documents/13/markdown' && method === 'GET') return Promise.resolve(markdownResponse())
        return Promise.resolve(jsonResponse({ detail: `unexpected ${method} ${url}` }, 500))
      })

      const w = await mountView('/documents/12')
      await w.find('[data-testid="edit-toggle"]').trigger('click')
      await flushPromises()
      expect(useMetadataEditMode().editMode.value).toBe(true)
      expect(w.find('#edit-title').exists()).toBe(true)

      // Simulate queue Prev/Next: the route changes but the view is not
      // remounted (same as an unkeyed <RouterView>).
      await router.push('/documents/13')
      await flushPromises()

      expect(useMetadataEditMode().editMode.value).toBe(false)
      expect(w.find('#edit-title').exists()).toBe(false)
      expect(rowValue(w, 'title')).toBe('Doc B')
    })
  })

  // --- ActionDock (Ask + lifted metadata Edit/Done) ----------------------------

  describe('ActionDock', () => {
    it('is absent while the hero still intersects the viewport', async () => {
      const w = await mountView()
      expect(w.find('[data-testid="action-dock"]').exists()).toBe(false)
    })

    it('appears once the IntersectionObserver reports the hero as not intersecting, and hides again when it returns', async () => {
      const w = await mountView()
      lastIntersectionObserver().trigger(false)
      await flushPromises()
      expect(w.find('[data-testid="action-dock"]').exists()).toBe(true)

      lastIntersectionObserver().trigger(true)
      await flushPromises()
      expect(w.find('[data-testid="action-dock"]').exists()).toBe(false)
    })

    it('disconnects the observer on unmount', async () => {
      const w = await mountView()
      const observer = lastIntersectionObserver()
      const disconnectSpy = vi.spyOn(observer, 'disconnect')
      w.unmount()
      expect(disconnectSpy).toHaveBeenCalled()
    })

    it('action-dock-edit-toggle flips the shared metadata edit mode, and the Details card reflects it', async () => {
      const w = await mountView()
      lastIntersectionObserver().trigger(false)
      await flushPromises()

      expect(w.find('[data-testid="action-dock-edit-toggle"]').text()).toContain('Edit')
      expect(w.find('#edit-title').exists()).toBe(false)

      await w.find('[data-testid="action-dock-edit-toggle"]').trigger('click')
      await flushPromises()

      expect(useMetadataEditMode().editMode.value).toBe(true)
      // The Details card's own toggle and inline editors reflect the same flag.
      expect(w.find('#edit-title').exists()).toBe(true)
      expect(w.find('[data-testid="edit-toggle"]').text()).toBe('Done')
      expect(w.find('[data-testid="action-dock-edit-toggle"]').text()).toContain('Done')

      // Toggling back off from the dock closes the card's editors too.
      await w.find('[data-testid="action-dock-edit-toggle"]').trigger('click')
      await flushPromises()
      expect(w.find('#edit-title').exists()).toBe(false)
    })

    it('action-dock-ask links to the same shared /ask href as the hero button', async () => {
      const w = await mountView()
      lastIntersectionObserver().trigger(false)
      await flushPromises()

      const dockAsk = w.find('[data-testid="action-dock-ask"]')
      const heroAsk = w.find('[data-testid="ask-about-document"]')
      expect(dockAsk.attributes('target')).toBe('_blank')
      expect(dockAsk.attributes('rel')).toContain('noopener')
      // Both anchors share the same computed href — one code path, two renders.
      expect(dockAsk.attributes('href')).toBe(heroAsk.attributes('href'))
      const url = new URL(dockAsk.attributes('href') ?? '', 'http://localhost')
      expect(url.pathname).toBe('/ask')
      expect(url.searchParams.get('q') ?? '').toContain('Energierekening mei 2026')
    })
  })

  describe('soft-deleted (trash) documents', () => {
    it('fetches the detail with include_deleted so a trashed doc opens read-only', async () => {
      detail = makeDetail({ deleted_at: '2026-07-01T09:00:00Z' })
      await mountView()
      const getUrls = fetchMock.mock.calls
        .filter((call) => ((call[1] as RequestInit | undefined)?.method ?? 'GET') === 'GET')
        .map((call) => String(call[0]))
      expect(getUrls).toContain('/api/documents/12?include_deleted=true')
    })

    it('renders the document and a trash banner instead of "Document not found"', async () => {
      // Regression: Recently Deleted links each title here; a soft-deleted doc
      // must render (with the banner), not the not-found error page.
      detail = makeDetail({ deleted_at: '2026-07-01T09:00:00Z' })
      const w = await mountView()

      expect(w.find('[data-testid="trash-banner"]').exists()).toBe(true)
      expect(w.find('h1').text()).toBe('Energierekening mei 2026')
      expect(w.text()).not.toContain('Document not found')
      // The soft-delete link is hidden (it would 404 on an already-deleted doc).
      expect(w.find('[data-testid="delete-link"]').exists()).toBe(false)
    })

    it('has no trash banner for a live document', async () => {
      const w = await mountView()
      expect(w.find('[data-testid="trash-banner"]').exists()).toBe(false)
    })

    it('threads include_deleted through the preview/download URLs', async () => {
      // Otherwise a trashed PDF/image opens read-only but its file 404s.
      detail = makeDetail({ deleted_at: '2026-07-01T09:00:00Z' })
      const w = await mountView()
      const href = w.find('[data-testid="download-original"]').attributes('href')
      expect(href).toContain('include_deleted=true')
    })

    it('restores from the banner: clears deleted_at and hides the banner', async () => {
      detail = makeDetail({ deleted_at: '2026-07-01T09:00:00Z' })
      const w = await mountView()

      await w.find('[data-testid="trash-restore"]').trigger('click')
      await flushPromises()

      const restoreCalls = fetchMock.mock.calls.filter(
        (call) => String(call[0]) === '/api/documents/12/restore',
      )
      expect(restoreCalls).toHaveLength(1)
      expect(w.find('[data-testid="trash-banner"]').exists()).toBe(false)
    })

    it('permanently deletes after confirmation and navigates to Recently Deleted', async () => {
      detail = makeDetail({ deleted_at: '2026-07-01T09:00:00Z' })
      const w = await mountView()
      const pushSpy = vi.spyOn(router, 'push')

      // Opening the dialog does not delete anything yet.
      await w.find('[data-testid="trash-purge"]').trigger('click')
      expect(
        fetchMock.mock.calls.some(
          (call) =>
            String(call[0]) === '/api/documents/12/permanent' &&
            (call[1] as RequestInit).method === 'DELETE',
        ),
      ).toBe(false)

      await w.find('[data-testid="confirm-accept"]').trigger('click')
      await flushPromises()

      expect(
        fetchMock.mock.calls.some(
          (call) =>
            String(call[0]) === '/api/documents/12/permanent' &&
            (call[1] as RequestInit).method === 'DELETE',
        ),
      ).toBe(true)
      expect(pushSpy).toHaveBeenCalledWith({ name: 'documents-deleted' })
    })
  })

  describe('binary original download (e.g. .docx)', () => {
    const DOCX_MIME =
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document'

    it('offers a download link for a .docx original shown as converted text', async () => {
      // A .docx has no image/pdf preview but its converted Markdown is readable;
      // the stored original must still be downloadable.
      detail = makeDetail({
        mime_type: DOCX_MIME,
        has_searchable_pdf: false,
        has_thumbnail: false,
      })
      const w = await mountView()

      const affordance = w.find('[data-testid="preview-download-original"]')
      expect(affordance.exists()).toBe(true)
      const link = w.find('[data-testid="download-original"]')
      expect(link.attributes('href')).toContain('/api/documents/12/original')
      // Not treated as an image/pdf preview.
      expect(w.find('[data-testid="preview-image"]').exists()).toBe(false)
      expect(w.find('[data-testid="preview-pdf"]').exists()).toBe(false)
    })

    it('does not show the download affordance for a text/markdown original', async () => {
      // A text original is shown verbatim in the reader; no separate download.
      detail = makeDetail({
        mime_type: 'text/markdown',
        has_searchable_pdf: false,
        has_thumbnail: false,
      })
      const w = await mountView()

      expect(w.find('[data-testid="preview-download-original"]').exists()).toBe(false)
    })
  })
})
