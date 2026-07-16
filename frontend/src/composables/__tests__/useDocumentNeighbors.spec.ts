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

describe('useDocumentNeighbors', () => {
  beforeEach(() => {
    localStorage.clear()
    listMock.mockReset()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('returns the surrounding ids for a mid-list document', async () => {
    listMock.mockResolvedValue(page([10, 20, 30, 40], 4))
    const { prevId, nextId } = useDocumentNeighbors(ref(20))
    await flushPromises()
    expect(prevId.value).toBe(10)
    expect(nextId.value).toBe(30)
  })

  it('has no previous for the first document', async () => {
    listMock.mockResolvedValue(page([10, 20, 30], 3))
    const { prevId, nextId } = useDocumentNeighbors(ref(10))
    await flushPromises()
    expect(prevId.value).toBeNull()
    expect(nextId.value).toBe(20)
  })

  it('has no next for the last document', async () => {
    listMock.mockResolvedValue(page([10, 20, 30], 3))
    const { prevId, nextId } = useDocumentNeighbors(ref(30))
    await flushPromises()
    expect(prevId.value).toBe(20)
    expect(nextId.value).toBeNull()
  })

  it('finds a neighbour across a page boundary', async () => {
    const firstPage = Array.from({ length: 100 }, (_, i) => i + 1) // ids 1..100
    listMock.mockImplementation((filters) =>
      Promise.resolve(
        (filters?.offset ?? 0) === 0 ? page(firstPage, 102, 0) : page([101, 102], 102, 100),
      ),
    )
    const { prevId, nextId } = useDocumentNeighbors(ref(100))
    await flushPromises()
    await flushPromises()
    expect(prevId.value).toBe(99)
    expect(nextId.value).toBe(101)
    // Two pages fetched to resolve the boundary.
    expect(listMock).toHaveBeenCalledTimes(2)
  })

  it('yields no neighbours when the document is absent from the list', async () => {
    listMock.mockResolvedValue(page([10, 20, 30], 3))
    const { prevId, nextId } = useDocumentNeighbors(ref(999))
    await flushPromises()
    expect(prevId.value).toBeNull()
    expect(nextId.value).toBeNull()
  })

  it('recomputes when the current id changes', async () => {
    listMock.mockResolvedValue(page([10, 20, 30, 40], 4))
    const id = ref(20)
    const { prevId, nextId } = useDocumentNeighbors(id)
    await flushPromises()
    expect(prevId.value).toBe(10)
    id.value = 30
    await flushPromises()
    expect(prevId.value).toBe(20)
    expect(nextId.value).toBe(40)
  })

  it('requests the list in the remembered sort order', async () => {
    localStorage.setItem(
      'library:doc-sort-v1',
      JSON.stringify({ sort: 'document_date', dir: 'asc' }),
    )
    listMock.mockResolvedValue(page([10, 20, 30], 3))
    useDocumentNeighbors(ref(20))
    await flushPromises()
    expect(listMock).toHaveBeenCalledWith(
      expect.objectContaining({ sort: 'document_date', direction: 'asc' }),
    )
  })
})
