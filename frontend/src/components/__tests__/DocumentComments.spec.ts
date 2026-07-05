import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

// DocumentComments only talks to the comment endpoints (comments themselves
// arrive via the `comments` prop, sourced from DocumentDetail) — stub the four
// mutation/list endpoints so the add/edit/delete flows can be asserted in
// isolation.
vi.mock('@/api/documents', () => ({
  listComments: vi.fn(),
  createComment: vi.fn(),
  updateComment: vi.fn(),
  deleteComment: vi.fn(),
}))

import { createComment, deleteComment, updateComment } from '@/api/documents'
import type { DocumentComment } from '@/api/documents'
import DocumentComments from '../DocumentComments.vue'

function makeComment(overrides: Partial<DocumentComment> = {}): DocumentComment {
  return {
    id: 1,
    document_id: 42,
    author_id: 7,
    body: 'First comment',
    created_at: '2026-06-01T10:00:00Z',
    ...overrides,
  }
}

describe('DocumentComments', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders each comment body and formatted date, newest first', () => {
    const comments = [
      makeComment({ id: 1, body: 'Older comment', created_at: '2026-06-01T10:00:00Z' }),
      makeComment({ id: 2, body: 'Newer comment', created_at: '2026-06-05T10:00:00Z' }),
    ]
    const wrapper = mount(DocumentComments, { props: { documentId: 42, comments } })

    expect(wrapper.find('[data-testid="document-comments"]').exists()).toBe(true)
    const item1 = wrapper.find('[data-testid="comment-item-1"]')
    const item2 = wrapper.find('[data-testid="comment-item-2"]')
    expect(item1.exists()).toBe(true)
    expect(item2.exists()).toBe(true)
    expect(item1.text()).toContain('Older comment')
    expect(item2.text()).toContain('Newer comment')
    // Newest-first ordering.
    const ids = wrapper.findAll('[data-testid^="comment-item-"]').map((el) => el.attributes('data-testid'))
    expect(ids).toEqual(['comment-item-2', 'comment-item-1'])
    // A formatted (not raw ISO) date is shown for each comment.
    expect(wrapper.text()).not.toContain('2026-06-01T10:00:00Z')
    expect(item2.text()).toContain('2026')
  })

  it('shows an empty state when there are no comments yet', () => {
    const wrapper = mount(DocumentComments, { props: { documentId: 42, comments: [] } })
    expect(wrapper.text().toLowerCase()).toContain('no comments')
  })

  it('adds a comment via createComment and emits changed', async () => {
    vi.mocked(createComment).mockResolvedValue(makeComment({ id: 9, body: 'New one' }))
    const wrapper = mount(DocumentComments, { props: { documentId: 42, comments: [] } })

    await wrapper.find('[data-testid="comment-add-body"]').setValue('New one')
    await wrapper.find('[data-testid="comment-add-submit"]').trigger('click')
    await flushPromises()

    expect(createComment).toHaveBeenCalledWith(42, 'New one')
    expect(wrapper.emitted('changed')).toHaveLength(1)
  })

  it('does not call createComment for a blank comment', async () => {
    const wrapper = mount(DocumentComments, { props: { documentId: 42, comments: [] } })

    await wrapper.find('[data-testid="comment-add-submit"]').trigger('click')
    await flushPromises()

    expect(createComment).not.toHaveBeenCalled()
    expect(wrapper.emitted('changed')).toBeUndefined()
  })

  it('edits a comment via updateComment and emits changed', async () => {
    const comment = makeComment({ id: 5, body: 'Original' })
    vi.mocked(updateComment).mockResolvedValue({ ...comment, body: 'Edited' })
    const wrapper = mount(DocumentComments, { props: { documentId: 42, comments: [comment] } })

    await wrapper.find('[data-testid="comment-edit-5"]').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="comment-edit-body"]').setValue('Edited')
    await wrapper.find('[data-testid="comment-edit-save"]').trigger('click')
    await flushPromises()

    expect(updateComment).toHaveBeenCalledWith(42, 5, 'Edited')
    expect(wrapper.emitted('changed')).toHaveLength(1)
  })

  it('cancels an in-progress edit without calling updateComment', async () => {
    const comment = makeComment({ id: 5, body: 'Original' })
    const wrapper = mount(DocumentComments, { props: { documentId: 42, comments: [comment] } })

    await wrapper.find('[data-testid="comment-edit-5"]').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="comment-edit-cancel"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="comment-edit-body"]').exists()).toBe(false)
    expect(updateComment).not.toHaveBeenCalled()
  })

  it('deletes a comment via deleteComment and emits changed', async () => {
    const comment = makeComment({ id: 5, body: 'Original' })
    vi.mocked(deleteComment).mockResolvedValue(undefined)
    const wrapper = mount(DocumentComments, { props: { documentId: 42, comments: [comment] } })

    await wrapper.find('[data-testid="comment-delete-5"]').trigger('click')
    await flushPromises()

    expect(deleteComment).toHaveBeenCalledWith(42, 5)
    expect(wrapper.emitted('changed')).toHaveLength(1)
  })
})
