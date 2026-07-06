import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useSavedViewsStore } from '../savedViews'
import type { SavedView } from '@/api/savedViews'

vi.mock('@/api/savedViews', () => ({
  listSavedViews: vi.fn(),
  createSavedView: vi.fn(),
  updateSavedView: vi.fn(),
  deleteSavedView: vi.fn(),
  reorderSavedViews: vi.fn(),
}))

import {
  createSavedView,
  deleteSavedView,
  listSavedViews,
  reorderSavedViews,
  updateSavedView,
} from '@/api/savedViews'

function view(overrides: Partial<SavedView> = {}): SavedView {
  return {
    id: 1,
    name: 'View',
    filter_state: {},
    pinned: false,
    sort_order: 0,
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
    ...overrides,
  }
}

describe('useSavedViewsStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('load() fetches once and caches (idempotent)', async () => {
    vi.mocked(listSavedViews).mockResolvedValue([view({ id: 5 })])
    const store = useSavedViewsStore()

    await store.load()
    await store.load() // second call is a no-op (already loaded)

    expect(listSavedViews).toHaveBeenCalledTimes(1)
    expect(store.views).toHaveLength(1)
    expect(store.loaded).toBe(true)
  })

  it('concurrent load() calls dedupe to a single request', async () => {
    vi.mocked(listSavedViews).mockResolvedValue([view()])
    const store = useSavedViewsStore()

    await Promise.all([store.load(), store.load(), store.load()])

    expect(listSavedViews).toHaveBeenCalledTimes(1)
  })

  it('load(true) forces a refetch', async () => {
    vi.mocked(listSavedViews).mockResolvedValue([view()])
    const store = useSavedViewsStore()
    await store.load()
    await store.load(true)
    expect(listSavedViews).toHaveBeenCalledTimes(2)
  })

  it('create() appends the created view', async () => {
    vi.mocked(createSavedView).mockResolvedValue(view({ id: 9, name: 'New' }))
    const store = useSavedViewsStore()

    await store.create({ name: 'New', filter_state: { kind: 'invoice' } })

    expect(createSavedView).toHaveBeenCalledWith({ name: 'New', filter_state: { kind: 'invoice' } })
    expect(store.views).toEqual([view({ id: 9, name: 'New' })])
  })

  it('update() replaces the matching view', async () => {
    vi.mocked(listSavedViews).mockResolvedValue([view({ id: 1, name: 'Old' })])
    vi.mocked(updateSavedView).mockResolvedValue(view({ id: 1, name: 'New' }))
    const store = useSavedViewsStore()
    await store.load()

    await store.update(1, { name: 'New' })

    expect(store.views[0]!.name).toBe('New')
  })

  it('remove() drops the view', async () => {
    vi.mocked(listSavedViews).mockResolvedValue([view({ id: 1 }), view({ id: 2 })])
    vi.mocked(deleteSavedView).mockResolvedValue(undefined)
    const store = useSavedViewsStore()
    await store.load()

    await store.remove(1)

    expect(store.views.map((v) => v.id)).toEqual([2])
  })

  it('reorder() replaces the list with the server response', async () => {
    vi.mocked(listSavedViews).mockResolvedValue([view({ id: 1 }), view({ id: 2 })])
    vi.mocked(reorderSavedViews).mockResolvedValue([
      view({ id: 2, sort_order: 0 }),
      view({ id: 1, sort_order: 1 }),
    ])
    const store = useSavedViewsStore()
    await store.load()

    await store.reorder([2, 1])

    expect(reorderSavedViews).toHaveBeenCalledWith([2, 1])
    expect(store.views.map((v) => v.id)).toEqual([2, 1])
  })

  it('pinnedViews returns only pinned views in sort order', async () => {
    vi.mocked(listSavedViews).mockResolvedValue([
      view({ id: 1, pinned: true, sort_order: 2 }),
      view({ id: 2, pinned: false, sort_order: 0 }),
      view({ id: 3, pinned: true, sort_order: 1 }),
    ])
    const store = useSavedViewsStore()
    await store.load()

    expect(store.pinnedViews.map((v) => v.id)).toEqual([3, 1])
  })
})
