import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import AskThreadBar from '../AskThreadBar.vue'
import { listThreads, type ThreadSummary } from '@/api/ask'

vi.mock('@/api/ask', () => ({ listThreads: vi.fn() }))

function summary(id: number, title: string): ThreadSummary {
  return { id, title, created_at: '', updated_at: '2026-10-20T10:00:00Z', turn_count: 1, total_cost_usd: 0 }
}

function mountBar(props: Partial<InstanceType<typeof AskThreadBar>['$props']> = {}) {
  return mount(AskThreadBar, {
    props: {
      title: 'Energy',
      threadId: 1,
      canStartNew: true,
      isLargeScreen: true,
      modes: [
        { value: 'conversation', label: 'Conversation' },
        { value: 'document', label: 'Document' },
      ],
      viewMode: 'conversation',
      ...props,
    },
    attachTo: document.body,
  })
}

describe('AskThreadBar', () => {
  beforeEach(() => {
    vi.mocked(listThreads).mockResolvedValue([summary(1, 'Energy'), summary(2, 'Car insurance')])
  })

  it('opens the switcher from the title, listing every conversation', async () => {
    const w = mountBar()
    expect(w.find('[data-testid="ask-switcher"]').exists()).toBe(false)
    await w.get('[data-testid="ask-title"]').trigger('click')
    await flushPromises()

    const items = w.findAll('[data-testid="thread-item"]')
    expect(items.map((i) => i.text())).toEqual([
      expect.stringContaining('Energy'),
      expect.stringContaining('Car insurance'),
    ])
    expect(items[0]!.attributes('aria-current')).toBe('true')
  })

  it('refetches on every open, so a conversation made elsewhere is never missing', async () => {
    const w = mountBar()
    await w.get('[data-testid="ask-title"]').trigger('click')
    await flushPromises()
    await w.get('[data-testid="ask-title"]').trigger('click')
    vi.mocked(listThreads).mockResolvedValue([summary(3, 'Taxes')])
    await w.get('[data-testid="ask-title"]').trigger('click')
    await flushPromises()

    expect(w.findAll('[data-testid="thread-item"]').map((i) => i.text())).toEqual([
      expect.stringContaining('Taxes'),
    ])
  })

  it('filters by title and says when nothing matches', async () => {
    const w = mountBar()
    await w.get('[data-testid="ask-title"]').trigger('click')
    await flushPromises()

    await w.get('[data-testid="thread-search"]').setValue('car')
    expect(w.findAll('[data-testid="thread-item"]')).toHaveLength(1)
    await w.get('[data-testid="thread-search"]').setValue('zzz')
    expect(w.find('[data-testid="thread-item"]').exists()).toBe(false)
    expect(w.get('[data-testid="thread-search-empty"]').text()).toContain('zzz')
  })

  it('selects another conversation and closes, but not the one already open', async () => {
    const w = mountBar()
    await w.get('[data-testid="ask-title"]').trigger('click')
    await flushPromises()
    await w.findAll('[data-testid="thread-item"]')[1]!.trigger('click')
    expect(w.emitted('select')).toEqual([[2]])
    expect(w.find('[data-testid="ask-switcher"]').exists()).toBe(false)

    await w.get('[data-testid="ask-title"]').trigger('click')
    await flushPromises()
    await w.findAll('[data-testid="thread-item"]')[0]!.trigger('click')
    expect(w.emitted('select')).toEqual([[2]])
  })

  it('the search icon opens the switcher with the search focused', async () => {
    const w = mountBar()
    await w.get('[data-testid="ask-search-conversations"]').trigger('click')
    await flushPromises()
    expect(document.activeElement).toBe(w.get('[data-testid="thread-search"]').element)
  })

  it('names a fresh conversation and offers no rename or delete for it', () => {
    const w = mountBar({ title: '', threadId: null, canStartNew: false })
    expect(w.get('[data-testid="ask-title"]').text()).toContain('New conversation')
    expect(w.find('[data-testid="ask-title-actions"]').exists()).toBe(false)
    expect(w.get('[data-testid="new-conversation"]').attributes('disabled')).toBeDefined()
  })

  it('says so when there are no conversations at all', async () => {
    vi.mocked(listThreads).mockResolvedValue([])
    const w = mountBar({ title: '', threadId: null })
    await w.get('[data-testid="ask-title"]').trigger('click')
    await flushPromises()
    expect(w.get('[data-testid="thread-list-empty"]').text()).toContain('No conversations yet')
  })

  it('does not emit a rename for an unchanged or blank title', async () => {
    const w = mountBar()
    await w.get('[data-testid="ask-title-actions"]').trigger('click')
    await w.get('[data-testid="thread-rename"]').trigger('click')
    await w.get('[data-testid="ask-title-rename-save"]').trigger('click')
    expect(w.emitted('rename')).toBeUndefined()
  })
})
