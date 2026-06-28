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

  it('emits new when the new-conversation button is clicked', async () => {
    const w = mount(ConversationSidebar, { props: { activeThreadId: null } })
    await flushPromises()
    await w.find('[data-testid="new-conversation"]').trigger('click')
    expect(w.emitted('new')).toBeTruthy()
  })

  it('deletes a thread and refreshes', async () => {
    vi.mocked(deleteThread).mockResolvedValue()
    const w = mount(ConversationSidebar, { props: { activeThreadId: 1 } })
    await flushPromises()
    await w.find('[data-testid="thread-delete"]').trigger('click')
    await flushPromises()
    expect(deleteThread).toHaveBeenCalledWith(1)
    expect(listThreads).toHaveBeenCalledTimes(2)
    expect(w.emitted('new')).toBeTruthy()
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
