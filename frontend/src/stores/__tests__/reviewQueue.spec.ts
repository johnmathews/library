import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useReviewQueueStore } from '../reviewQueue'

const listDocuments = vi.fn()
vi.mock('@/api/documents', () => ({
  listDocuments: (...args: unknown[]) => listDocuments(...args),
}))

function listBody(ids: number[]) {
  return { items: ids.map((id) => ({ id })), total: ids.length, limit: 200, offset: 0 }
}

describe('useReviewQueueStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    listDocuments.mockReset()
  })

  it('start() loads needs-review ids and points at the first', async () => {
    listDocuments.mockResolvedValue(listBody([5, 8, 13]))
    const q = useReviewQueueStore()

    const first = await q.start()

    expect(listDocuments).toHaveBeenCalledWith({ review_status: 'needs_review', limit: 200, offset: 0 })
    expect(first).toBe(5)
    expect(q.currentId).toBe(5)
    expect(q.total).toBe(3)
    expect(q.position).toBe(1)
    expect(q.hasPrev).toBe(false)
    expect(q.hasNext).toBe(true)
  })

  it('next()/prev() step the cursor and stop at the ends', async () => {
    listDocuments.mockResolvedValue(listBody([5, 8]))
    const q = useReviewQueueStore()
    await q.start()

    expect(q.next()).toBe(8)
    expect(q.position).toBe(2)
    expect(q.hasNext).toBe(false)
    // Past the end -> null (queue finished).
    expect(q.next()).toBeNull()
    expect(q.prev()).toBe(5)
    expect(q.prev()).toBe(5) // clamped at the start
  })

  it('resolveCurrent() drops the current doc and lands on the next', async () => {
    listDocuments.mockResolvedValue(listBody([5, 8, 13]))
    const q = useReviewQueueStore()
    await q.start()

    // Resolve #5 -> now on #8, two left.
    expect(q.resolveCurrent()).toBe(8)
    expect(q.total).toBe(2)
    expect(q.currentId).toBe(8)
  })

  it('resolveCurrent() on the last doc returns null (queue empty)', async () => {
    listDocuments.mockResolvedValue(listBody([5]))
    const q = useReviewQueueStore()
    await q.start()

    expect(q.resolveCurrent()).toBeNull()
    expect(q.isActive).toBe(false)
    expect(q.position).toBe(0)
  })

  it('resolveCurrent() at the end of a longer queue steps back to the new last', async () => {
    listDocuments.mockResolvedValue(listBody([5, 8, 13]))
    const q = useReviewQueueStore()
    await q.start()
    q.next()
    q.next() // on #13 (last)

    expect(q.resolveCurrent()).toBe(8) // 13 removed, cursor clamps to new last
    expect(q.total).toBe(2)
  })
})
