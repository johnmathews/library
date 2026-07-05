import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

// The notes API is the only external dependency of this panel — stub the three
// endpoints it calls so the two-channel write-back contract can be asserted in
// isolation.
vi.mock('@/api/notes', () => ({
  updateNote: vi.fn(),
  listNoteVersions: vi.fn(),
  restoreNoteVersion: vi.fn(),
}))

import { listNoteVersions, restoreNoteVersion, updateNote } from '@/api/notes'
import type { DocumentDetail } from '@/api/documents'
import NoteEditorPanel from '../NoteEditorPanel.vue'

function makeNote(overrides: Partial<DocumentDetail> = {}): DocumentDetail {
  return {
    id: 12,
    title: 'My note',
    summary: null,
    kind: null,
    sender: null,
    recipient: null,
    tags: [],
    projects: [],
    topics: [],
    document_date: null,
    language: 'eng',
    status: 'indexed',
    mime_type: 'text/markdown',
    page_count: 1,
    created_at: '2026-06-10T12:00:00Z',
    updated_at: '2026-06-11T09:30:00Z',
    has_searchable_pdf: false,
    has_thumbnail: false,
    snippet: null,
    rank: null,
    ocr_text: null,
    ocr_confidence: null,
    amount_total: null,
    currency: null,
    due_date: null,
    expiry_date: null,
    source: 'note',
    original_filename: null,
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

function mountPanel(noteBody = 'Old title\nold body') {
  const doc = makeNote()
  const wrapper = mount(NoteEditorPanel, { props: { doc, noteBody } })
  return { wrapper, doc }
}

describe('NoteEditorPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })

  it('seeds the draft body from the noteBody prop when the editor opens', async () => {
    const { wrapper } = mountPanel('Seed title\nseed body')

    await wrapper.find('[data-testid="note-edit-button"]').trigger('click')
    await flushPromises()

    const body = wrapper.find('#note-edit-body').element as HTMLTextAreaElement
    expect(body.value).toBe('Seed title\nseed body')
    // The title is the first line of the body — there is no separate title input.
    expect(wrapper.find('#note-edit-title').exists()).toBe(false)
  })

  it('emits BOTH update:doc (fresh doc) AND reload-markdown when a note is saved', async () => {
    const fresh = makeNote({ title: 'Updated note' })
    vi.mocked(updateNote).mockResolvedValue(fresh)
    const { wrapper, doc } = mountPanel()

    await wrapper.find('[data-testid="note-edit-button"]').trigger('click')
    await flushPromises()
    await wrapper.find('#note-edit-body').setValue('Updated note\nupdated body')
    await wrapper.find('[data-testid="note-edit-save"]').trigger('click')
    await flushPromises()

    expect(updateNote).toHaveBeenCalledWith(doc.id, {
      title: 'Updated note',
      body_markdown: 'Updated note\nupdated body',
    })
    // Channel 1: the fresh DocumentDetail. Channel 2: re-fetch the reader body.
    const updates = wrapper.emitted('update:doc')
    expect(updates).toHaveLength(1)
    expect(updates![0]![0]).toBe(fresh)
    expect(wrapper.emitted('reload-markdown')).toHaveLength(1)
  })

  it('loads versions on toggle and renders the version/restore controls', async () => {
    vi.mocked(listNoteVersions).mockResolvedValue([
      { version_no: 2, title: 'v2', body: 'b2', created_at: '2026-06-02T00:00:00Z' },
      { version_no: 1, title: 'v1', body: 'b1', created_at: '2026-06-01T00:00:00Z' },
    ])
    const { wrapper, doc } = mountPanel()

    await wrapper.find('[data-testid="note-versions-toggle"]').trigger('click')
    await flushPromises()

    expect(listNoteVersions).toHaveBeenCalledWith(doc.id)
    expect(wrapper.find('[data-testid="note-version-2"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="note-version-1"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="note-restore-2"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="note-restore-1"]').exists()).toBe(true)
  })

  it('emits BOTH update:doc (fresh doc) AND reload-markdown when a version is restored', async () => {
    vi.mocked(listNoteVersions).mockResolvedValue([
      { version_no: 1, title: 'v1', body: 'b1', created_at: '2026-06-01T00:00:00Z' },
    ])
    const restored = makeNote({ title: 'Restored note' })
    vi.mocked(restoreNoteVersion).mockResolvedValue(restored)
    const { wrapper, doc } = mountPanel()

    await wrapper.find('[data-testid="note-versions-toggle"]').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="note-restore-1"]').trigger('click')
    await flushPromises()

    expect(restoreNoteVersion).toHaveBeenCalledWith(doc.id, 1)
    const updates = wrapper.emitted('update:doc')
    expect(updates).toHaveLength(1)
    expect(updates![0]![0]).toBe(restored)
    expect(wrapper.emitted('reload-markdown')).toHaveLength(1)
  })
})
