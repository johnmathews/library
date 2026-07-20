import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { ref } from 'vue'
import { useDocumentNeighbors } from '@/composables/useDocumentNeighbors'
import { listDocuments, type DocumentListResponse } from '@/api/documents'

vi.mock('@/api/documents', () => ({ listDocuments: vi.fn() }))
const listMock = vi.mocked(listDocuments)

/** Build a list response of the given ids at an offset. */
function page(ids: number[], total: number, offset = 0): DocumentListResponse {
  return { items: ids.map((id) => ({ id }) as never), total, limit: 100, offset }
}

// Neighbours are computed by document id, from a scan sorted `added_date desc`
// (id-descending in practice), so test data is supplied highest-id-first.

describe('useDocumentNeighbors', () => {
  beforeEach(() => {
    localStorage.clear()
    listMock.mockReset()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('returns the id either side (N-1 / N+1) for a mid-list document', async () => {
    listMock.mockResolvedValue(page([40, 30, 20, 10], 4))
    const { prevId, nextId } = useDocumentNeighbors(ref(20))
    await flushPromises()
    expect(prevId.value).toBe(10) // next-lower id
    expect(nextId.value).toBe(30) // next-higher id
  })

  it('has no previous for the lowest id', async () => {
    listMock.mockResolvedValue(page([30, 20, 10], 3))
    const { prevId, nextId } = useDocumentNeighbors(ref(10))
    await flushPromises()
    expect(prevId.value).toBeNull()
    expect(nextId.value).toBe(20)
  })

  it('has no next for the highest id', async () => {
    listMock.mockResolvedValue(page([30, 20, 10], 3))
    const { prevId, nextId } = useDocumentNeighbors(ref(30))
    await flushPromises()
    expect(prevId.value).toBe(20)
    expect(nextId.value).toBeNull()
  })

  it('computes the nearest ids by value, not raw list adjacency', async () => {
    // Gaps in the id sequence: N=20's neighbours are 15 and 25, not 10/30.
    listMock.mockResolvedValue(page([30, 25, 20, 15, 10], 5))
    const { prevId, nextId } = useDocumentNeighbors(ref(20))
    await flushPromises()
    expect(prevId.value).toBe(15)
    expect(nextId.value).toBe(25)
  })

  it('finds the lower neighbour across a page boundary', async () => {
    // Descending scan: all ids above the current one land on the first page;
    // the next-lower id sits on the second, forcing a second fetch.
    const firstPage = Array.from({ length: 100 }, (_, i) => 105 - i) // 105..6
    listMock.mockImplementation((filters) =>
      Promise.resolve(
        (filters?.offset ?? 0) === 0 ? page(firstPage, 105, 0) : page([5, 4, 3, 2, 1], 105, 100),
      ),
    )
    const { prevId, nextId } = useDocumentNeighbors(ref(6))
    await flushPromises()
    await flushPromises()
    expect(prevId.value).toBe(5)
    expect(nextId.value).toBe(7)
    expect(listMock).toHaveBeenCalledTimes(2)
  })

  it('yields no neighbour on the side that runs off the list', async () => {
    // id above everything present: a previous exists, no next.
    listMock.mockResolvedValue(page([30, 20, 10], 3))
    const { prevId, nextId } = useDocumentNeighbors(ref(999))
    await flushPromises()
    expect(prevId.value).toBe(30)
    expect(nextId.value).toBeNull()
  })

  it('recomputes when the current id changes', async () => {
    listMock.mockResolvedValue(page([40, 30, 20, 10], 4))
    const id = ref(20)
    const { prevId, nextId } = useDocumentNeighbors(id)
    await flushPromises()
    expect(prevId.value).toBe(10)
    id.value = 30
    await flushPromises()
    expect(prevId.value).toBe(20)
    expect(nextId.value).toBe(40)
  })

  it('scans by added_date desc regardless of the list view sort preference', async () => {
    // The remembered list sort must not affect id-based neighbour navigation.
    localStorage.setItem(
      'library:doc-sort-v1',
      JSON.stringify({ sort: 'document_date', dir: 'asc' }),
    )
    listMock.mockResolvedValue(page([30, 20, 10], 3))
    useDocumentNeighbors(ref(20))
    await flushPromises()
    expect(listMock).toHaveBeenCalledWith(
      expect.objectContaining({ sort: 'added_date', direction: 'desc' }),
    )
  })
})
