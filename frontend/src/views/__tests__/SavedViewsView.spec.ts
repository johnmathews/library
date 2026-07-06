import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, RouterLinkStub } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@/api/savedViews', () => ({
  listSavedViews: vi.fn(),
  createSavedView: vi.fn(),
  updateSavedView: vi.fn(),
  deleteSavedView: vi.fn(),
  reorderSavedViews: vi.fn(),
}))

import {
  deleteSavedView,
  listSavedViews,
  reorderSavedViews,
  updateSavedView,
} from '@/api/savedViews'
import type { SavedView } from '@/api/savedViews'
import SavedViewsView from '../SavedViewsView.vue'

function view(overrides: Partial<SavedView> = {}): SavedView {
  return {
    id: 1,
    name: 'Unpaid invoices',
    filter_state: { kind: 'invoice' },
    pinned: false,
    sort_order: 0,
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
    ...overrides,
  }
}

const VIEWS = [
  view({ id: 1, name: 'Unpaid invoices', sort_order: 0, pinned: true }),
  view({ id: 2, name: 'Tax 2026', sort_order: 1, filter_state: { tag: 'tax' } }),
]

function mountView() {
  return mount(SavedViewsView, { global: { stubs: { RouterLink: RouterLinkStub } } })
}

describe('SavedViewsView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.mocked(listSavedViews).mockResolvedValue(structuredClone(VIEWS))
    vi.mocked(updateSavedView).mockImplementation((id, patch) =>
      Promise.resolve(view({ id, ...patch } as Partial<SavedView>)),
    )
    vi.mocked(deleteSavedView).mockResolvedValue(undefined)
    vi.mocked(reorderSavedViews).mockResolvedValue(structuredClone(VIEWS))
  })
  afterEach(() => vi.clearAllMocks())

  it('renders a row per view with a link carrying the saved query', async () => {
    const w = mountView()
    await flushPromises()
    expect(w.findAll('[data-testid="saved-view-row"]')).toHaveLength(2)
    const link = w.findComponent(RouterLinkStub)
    expect(w.get('[data-testid="saved-view-name-1"]').text()).toBe('Unpaid invoices')
    // The first row's link targets the homepage with the saved filter state.
    expect(link.props('to')).toEqual({
      path: '/',
      query: { kind: 'invoice' },
    })
  })

  it('shows the empty state when there are no views', async () => {
    vi.mocked(listSavedViews).mockResolvedValue([])
    const w = mountView()
    await flushPromises()
    expect(w.find('[data-testid="saved-views-empty"]').exists()).toBe(true)
    expect(w.find('[data-testid="saved-views-list"]').exists()).toBe(false)
  })

  it('renames a view via inline edit', async () => {
    const w = mountView()
    await flushPromises()
    await w.get('[data-testid="rename-view-1"]').trigger('click')
    await w.get('[data-testid="rename-input-1"]').setValue('Overdue')
    await w.get('[data-testid="rename-save-1"]').trigger('click')
    await flushPromises()
    expect(updateSavedView).toHaveBeenCalledWith(1, { name: 'Overdue' })
  })

  it('toggles pin state', async () => {
    const w = mountView()
    await flushPromises()
    // View 2 is unpinned → clicking pins it.
    await w.get('[data-testid="toggle-pin-2"]').trigger('click')
    await flushPromises()
    expect(updateSavedView).toHaveBeenCalledWith(2, { pinned: true })
  })

  it('deletes a view only after a confirm step', async () => {
    const w = mountView()
    await flushPromises()
    await w.get('[data-testid="delete-view-1"]').trigger('click')
    expect(deleteSavedView).not.toHaveBeenCalled()
    expect(w.find('[data-testid="delete-confirm-1"]').exists()).toBe(true)
    await w.get('[data-testid="delete-confirm-1"]').trigger('click')
    await flushPromises()
    expect(deleteSavedView).toHaveBeenCalledWith(1)
  })

  it('reorders with the full reordered id list when moving a view down', async () => {
    const w = mountView()
    await flushPromises()
    await w.get('[data-testid="view-down-1"]').trigger('click')
    await flushPromises()
    expect(reorderSavedViews).toHaveBeenCalledWith([2, 1])
  })

  it('disables the up button on the first row and down on the last', async () => {
    const w = mountView()
    await flushPromises()
    expect(w.get('[data-testid="view-up-1"]').attributes('disabled')).toBeDefined()
    expect(w.get('[data-testid="view-down-2"]').attributes('disabled')).toBeDefined()
  })
})
