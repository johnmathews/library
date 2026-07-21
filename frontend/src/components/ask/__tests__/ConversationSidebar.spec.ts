import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ConversationSidebar from '../ConversationSidebar.vue'
import { listThreads, deleteThread, renameThread } from '@/api/ask'

vi.mock('@/api/ask', () => ({
  listThreads: vi.fn(),
  deleteThread: vi.fn(),
  renameThread: vi.fn(),
}))

describe('ConversationSidebar', () => {
  beforeEach(() => {
    vi.mocked(listThreads).mockResolvedValue([
      { id: 1, title: 'Energy bills', created_at: '', updated_at: '', turn_count: 3, total_cost_usd: 0.05 },
    ])
  })
  afterEach(() => vi.clearAllMocks())

  /** Rename/Delete live behind a per-row "⋯" overflow menu now; open it first. */
  async function openMenu(w: VueWrapper): Promise<void> {
    await w.find('[data-testid="thread-actions-menu"]').trigger('click')
  }

  it('lists threads and emits select when one is clicked', async () => {
    const w = mount(ConversationSidebar, { props: { activeThreadId: null } })
    await flushPromises()
    expect(w.text()).toContain('Energy bills')
    await w.find('[data-testid="thread-item"]').trigger('click')
    expect(w.emitted('select')?.[0]).toEqual([1])
  })

  it('emits threads-changed with the loaded thread count', async () => {
    const w = mount(ConversationSidebar, { props: { activeThreadId: null } })
    await flushPromises()
    expect(w.emitted('threads-changed')?.[0]).toEqual([1])
  })

  it('emits new when the new-conversation button is clicked', async () => {
    const w = mount(ConversationSidebar, { props: { activeThreadId: null } })
    await flushPromises()
    await w.find('[data-testid="new-conversation"]').trigger('click')
    expect(w.emitted('new')).toBeTruthy()
  })

  it('disables the new-conversation button and does not emit new when newDisabled (item 2)', async () => {
    const w = mount(ConversationSidebar, { props: { activeThreadId: null, newDisabled: true } })
    await flushPromises()
    const btn = w.find('[data-testid="new-conversation"]')
    // The button carries the real disabled attribute (keyboard/AT inert), and a
    // click cannot start a redundant "new conversation" from the fresh state.
    expect((btn.element as HTMLButtonElement).disabled).toBe(true)
    await btn.trigger('click')
    expect(w.emitted('new')).toBeFalsy()
  })

  it('opens the ⋯ menu and requires confirmation before deleting, then deletes and refreshes (W2)', async () => {
    vi.mocked(deleteThread).mockResolvedValue()
    const w = mount(ConversationSidebar, { props: { activeThreadId: 1 } })
    await flushPromises()

    // The row shows a ⋯ menu, not always-on Rename/Delete links.
    expect(w.find('[data-testid="thread-actions-menu"]').exists()).toBe(true)
    await openMenu(w)

    // The menu's Delete only asks for confirmation — nothing is deleted yet.
    await w.find('[data-testid="thread-delete"]').trigger('click')
    expect(deleteThread).not.toHaveBeenCalled()
    expect(w.find('[data-testid="thread-delete-confirm"]').exists()).toBe(true)

    // Confirming performs the delete and refreshes.
    await w.find('[data-testid="thread-delete-confirm"]').trigger('click')
    await flushPromises()
    expect(deleteThread).toHaveBeenCalledWith(1)
    expect(listThreads).toHaveBeenCalledTimes(2)
    expect(w.emitted('new')).toBeTruthy()
  })

  it('cancels a pending delete without calling the API (W2)', async () => {
    vi.mocked(deleteThread).mockResolvedValue()
    const w = mount(ConversationSidebar, { props: { activeThreadId: 1 } })
    await flushPromises()
    await openMenu(w)
    await w.find('[data-testid="thread-delete"]').trigger('click')
    await w.find('[data-testid="thread-delete-cancel"]').trigger('click')
    expect(deleteThread).not.toHaveBeenCalled()
    // Back to the ⋯ menu affordance.
    expect(w.find('[data-testid="thread-actions-menu"]').exists()).toBe(true)
    expect(w.find('[data-testid="thread-delete-confirm"]').exists()).toBe(false)
  })

  it('renames a thread inline from the ⋯ menu, then refreshes', async () => {
    vi.mocked(renameThread).mockResolvedValue({
      id: 1,
      title: 'Utility costs',
      created_at: '',
      updated_at: '',
      turn_count: 3,
      total_cost_usd: 0.05,
    })
    const w = mount(ConversationSidebar, { props: { activeThreadId: 1 } })
    await flushPromises()

    // Arm the inline editor via the menu: an input seeded with the current title.
    await openMenu(w)
    await w.find('[data-testid="thread-rename"]').trigger('click')
    const input = w.find('[data-testid="thread-rename-input"]')
    expect(input.exists()).toBe(true)
    expect((input.element as HTMLInputElement).value).toBe('Energy bills')

    // Save the new title → PATCH with the trimmed value, then re-list.
    await input.setValue('  Utility costs  ')
    await w.find('[data-testid="thread-rename-save"]').trigger('click')
    await flushPromises()
    expect(renameThread).toHaveBeenCalledWith(1, 'Utility costs')
    expect(listThreads).toHaveBeenCalledTimes(2)
  })

  it('does not select the row when clicking inside the rename input', async () => {
    const w = mount(ConversationSidebar, { props: { activeThreadId: 1 } })
    await flushPromises()
    await openMenu(w)
    await w.find('[data-testid="thread-rename"]').trigger('click')
    await w.find('[data-testid="thread-rename-input"]').trigger('click')
    expect(w.emitted('select')).toBeFalsy()
  })

  it('cancels a rename without calling the API', async () => {
    const w = mount(ConversationSidebar, { props: { activeThreadId: 1 } })
    await flushPromises()
    await openMenu(w)
    await w.find('[data-testid="thread-rename"]').trigger('click')
    await w.find('[data-testid="thread-rename-input"]').setValue('Something else')
    await w.find('[data-testid="thread-rename-cancel"]').trigger('click')
    expect(renameThread).not.toHaveBeenCalled()
    // Back to the label + ⋯ affordance.
    expect(w.find('[data-testid="thread-rename-input"]').exists()).toBe(false)
    expect(w.text()).toContain('Energy bills')
  })

  it('skips the API on a blank or unchanged rename', async () => {
    const w = mount(ConversationSidebar, { props: { activeThreadId: 1 } })
    await flushPromises()
    // Unchanged title → no request, editor closes.
    await openMenu(w)
    await w.find('[data-testid="thread-rename"]').trigger('click')
    await w.find('[data-testid="thread-rename-save"]').trigger('click')
    await flushPromises()
    // Blank title → no request either.
    await openMenu(w)
    await w.find('[data-testid="thread-rename"]').trigger('click')
    await w.find('[data-testid="thread-rename-input"]').setValue('   ')
    await w.find('[data-testid="thread-rename-save"]').trigger('click')
    await flushPromises()
    expect(renameThread).not.toHaveBeenCalled()
  })

  it('marks the active thread with a full-perimeter ring, not a left-only bar (W1)', async () => {
    const w = mount(ConversationSidebar, { props: { activeThreadId: 1 } })
    await flushPromises()
    const item = w.find('[data-testid="thread-item"]')
    // A ring draws on all four sides; the old left-accent bar is gone.
    expect(item.classes()).toContain('ring-1')
    expect(item.classes()).toContain('ring-violet-500')
    expect(item.classes()).not.toContain('border-l-2')
  })

  it('does not ring an inactive thread (W1)', async () => {
    const w = mount(ConversationSidebar, { props: { activeThreadId: 999 } })
    await flushPromises()
    const item = w.find('[data-testid="thread-item"]')
    expect(item.classes()).toContain('ring-transparent')
    expect(item.classes()).not.toContain('ring-violet-500')
  })

  it('filters threads by the search query (W10)', async () => {
    vi.mocked(listThreads).mockResolvedValue([
      { id: 1, title: 'Energy bills', created_at: '', updated_at: '', turn_count: 3, total_cost_usd: 0 },
      { id: 2, title: 'Tax documents', created_at: '', updated_at: '', turn_count: 1, total_cost_usd: 0 },
    ])
    const w = mount(ConversationSidebar, { props: { activeThreadId: null } })
    await flushPromises()
    expect(w.findAll('[data-testid="thread-item"]')).toHaveLength(2)
    await w.find('[data-testid="thread-search"]').setValue('tax')
    expect(w.findAll('[data-testid="thread-item"]')).toHaveLength(1)
    expect(w.text()).toContain('Tax documents')
    expect(w.text()).not.toContain('Energy bills')
  })
})
