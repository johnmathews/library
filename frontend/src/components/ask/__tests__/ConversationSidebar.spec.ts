import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ConversationSidebar from '../ConversationSidebar.vue'
import { listThreads, deleteThread } from '@/api/ask'

vi.mock('@/api/ask', () => ({ listThreads: vi.fn(), deleteThread: vi.fn() }))

describe('ConversationSidebar', () => {
  beforeEach(() => {
    vi.mocked(listThreads).mockResolvedValue([
      { id: 1, title: 'Energy bills', created_at: '', updated_at: '', turn_count: 3, total_cost_usd: 0.05 },
    ])
  })
  afterEach(() => vi.clearAllMocks())

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

  it('requires confirmation before deleting, then deletes and refreshes (W3)', async () => {
    vi.mocked(deleteThread).mockResolvedValue()
    const w = mount(ConversationSidebar, { props: { activeThreadId: 1 } })
    await flushPromises()

    // First click only asks for confirmation — nothing is deleted yet.
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

  it('cancels a pending delete without calling the API (W3)', async () => {
    vi.mocked(deleteThread).mockResolvedValue()
    const w = mount(ConversationSidebar, { props: { activeThreadId: 1 } })
    await flushPromises()
    await w.find('[data-testid="thread-delete"]').trigger('click')
    await w.find('[data-testid="thread-delete-cancel"]').trigger('click')
    expect(deleteThread).not.toHaveBeenCalled()
    // Back to the plain Delete affordance.
    expect(w.find('[data-testid="thread-delete"]').exists()).toBe(true)
    expect(w.find('[data-testid="thread-delete-confirm"]').exists()).toBe(false)
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
