import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

// Keep DOCUMENT_LANGUAGES (and the rest) real; stub only the write endpoint.
vi.mock('@/api/documents', async () => {
  const actual = await vi.importActual<typeof import('@/api/documents')>('@/api/documents')
  return { ...actual, updateDocument: vi.fn() }
})

// Taxonomy lists feed the kind/recipient selects + sender datalist.
vi.mock('@/api/taxonomy', () => ({
  listKinds: vi.fn(),
  createKind: vi.fn(),
  listSenders: vi.fn(),
  listRecipients: vi.fn(),
}))

// The shared taxonomy cache (projects multiselect + refresh-after-inline-add).
vi.mock('@/composables/taxonomyOptions', () => ({
  refreshTaxonomyOptions: vi.fn().mockResolvedValue(undefined),
  useTaxonomyOptions: () => ({
    projects: ref([]),
    ensureLoaded: vi.fn().mockResolvedValue(undefined),
  }),
}))

import { ref } from 'vue'
import { updateDocument, type DocumentDetail } from '@/api/documents'
import { listKinds, listRecipients, listSenders } from '@/api/taxonomy'
import { useMetadataEditMode } from '@/composables/useMetadataEditMode'
import DocumentMetadataEditor from '../DocumentMetadataEditor.vue'

const KINDS = [
  { slug: 'invoice', name: 'Invoice', document_count: 3 },
  { slug: 'receipt', name: 'Receipt', document_count: 0 },
]
const SENDERS = [{ id: 3, name: 'Eneco', document_count: 3 }]
const RECIPIENTS = [
  { id: 5, name: 'John', document_count: 7 },
  { id: 6, name: 'Wife', document_count: 2 },
]

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
    matters: [],
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

type Section = 'content' | 'classification' | 'parties' | 'financial' | 'system'

function mountEditor(section: Section = 'content', doc: DocumentDetail = makeDetail()) {
  const wrapper = mount(DocumentMetadataEditor, {
    props: { doc, section },
    global: { stubs: { RouterLink: true } },
  })
  return { wrapper, doc }
}

/** Enter the shared page-wide metadata edit mode. The Edit toggle now lives in
 * the detail view's hero (not on any tile), so tests flip the same singleton the
 * hero button flips, exactly as the Action dock does. */
async function enterEditMode() {
  useMetadataEditMode().setEditMode(true)
  await flushPromises()
}

describe('DocumentMetadataEditor', () => {
  beforeEach(() => {
    // editMode is a module singleton (useMetadataEditMode) — reset it so a
    // toggle in one test never leaks "already editing" into the next.
    useMetadataEditMode().setEditMode(false)
    vi.clearAllMocks()
    vi.mocked(updateDocument).mockReset()
    vi.mocked(listKinds).mockResolvedValue(structuredClone(KINDS))
    vi.mocked(listSenders).mockResolvedValue(structuredClone(SENDERS))
    vi.mocked(listRecipients).mockResolvedValue(structuredClone(RECIPIENTS))
  })

  it('autosaves a field on commit and emits update:doc with the fresh doc', async () => {
    const fresh = makeDetail({ title: 'Nieuwe titel' })
    vi.mocked(updateDocument).mockResolvedValue(fresh)
    const { wrapper, doc } = mountEditor()
    await flushPromises()

    await enterEditMode()
    // AppInput's native @change commits the field; VTU's setValue dispatches it.
    await wrapper.find('#edit-title').setValue('Nieuwe titel')
    await flushPromises()

    expect(updateDocument).toHaveBeenCalledWith(doc.id, { title: 'Nieuwe titel' })
    const updates = wrapper.emitted('update:doc')
    expect(updates).toHaveLength(1)
    expect(updates![0]![0]).toBe(fresh)
  })

  it('guards against a concurrent second save of the same field while one is in flight', async () => {
    // Hold the PATCH open so the field stays "saving" across the second commit.
    let resolveUpdate: (doc: DocumentDetail) => void = () => {}
    vi.mocked(updateDocument).mockImplementation(
      () => new Promise((resolve) => (resolveUpdate = resolve)),
    )
    const { wrapper } = mountEditor()
    await flushPromises()

    await enterEditMode()
    await wrapper.find('#edit-title').setValue('Nieuwe titel') // first save — now pending
    await wrapper.find('#edit-title').trigger('change') // second save — must no-op
    await flushPromises()

    expect(updateDocument).toHaveBeenCalledTimes(1)

    resolveUpdate(makeDetail({ title: 'Nieuwe titel' }))
    await flushPromises()
  })

  it('does not clobber an in-progress draft when the doc prop changes mid-edit', async () => {
    // GOTCHA: drafts hydrate ONLY on the Edit toggle, never on a prop change —
    // an external SSE/refresh replacing `doc` must not overwrite the user's typing.
    // Hold the incidental autosave open so it can't hydrate the draft back either;
    // this isolates the prop-change path as the thing under test.
    vi.mocked(updateDocument).mockImplementation(() => new Promise(() => {}))
    const { wrapper } = mountEditor()
    await flushPromises()

    await enterEditMode()
    await wrapper.find('#edit-title').setValue('Half-typed edit')

    // An outside update lands while the field is dirty and edit mode is open.
    await wrapper.setProps({ doc: makeDetail({ title: 'Server-pushed title' }) })
    await flushPromises()

    // The draft is pinned to the user's typing, not the incoming prop value.
    expect((wrapper.find('#edit-title').element as HTMLInputElement).value).toBe('Half-typed edit')
  })

  it('hydrates drafts when the shared edit mode is flipped by an external caller (e.g. the Action dock), not just this card\'s own toggle', async () => {
    // Regression test: the shared `useMetadataEditMode` flag can be flipped by
    // DocumentDetailView's floating Action dock as well as this card's own
    // `edit-toggle` button. Flip it directly here (bypassing the card's own
    // button entirely) to simulate the Action dock's path.
    const { wrapper, doc } = mountEditor('content')
    await flushPromises()

    // Flip the shared flag directly (bypassing any button) to simulate the dock.
    useMetadataEditMode().editMode.value = true
    await flushPromises()

    expect((wrapper.find('#edit-title').element as HTMLInputElement).value).toBe(doc.title)

    // A sibling section tile (Sender & dates) hydrates from the same flag — and,
    // if it only mounts once editing is already on, still hydrates on mount.
    const parties = mountEditor('parties', doc)
    await flushPromises()
    expect((parties.wrapper.find('#edit-sender').element as HTMLInputElement).value).toBe(
      doc.sender?.name ?? '',
    )
    // Entering edit mode alone (no field committed) must never autosave.
    expect(updateDocument).not.toHaveBeenCalled()
  })

  it('renders the sender datalist adjacent to the sender input', async () => {
    const { wrapper } = mountEditor('parties')
    await flushPromises()

    await enterEditMode()

    expect(wrapper.find('#edit-sender').attributes('list')).toBe('sender-options')
    const options = wrapper.find('datalist#sender-options').findAll('option')
    expect(options.map((option) => option.attributes('value'))).toEqual(['Eneco'])
  })
})
