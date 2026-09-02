import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { ref } from 'vue'
import { useDocumentNeighbors } from '@/composables/useDocumentNeighbors'
import { listDocuments, type DocumentListResponse } from '@/api/documents'
import { parseDocumentQuery, type AppliedFilters } from '@/utils/documentQuery'

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

  // --- Following the active filter set (#140) --------------------------------
  //
  // The set the user is walking, not the whole archive. Order WITHIN the set is
  // unchanged (still id-ascending) — this changes membership, not direction,
  // which is why it does not reopen the settled decision against following the
  // list's SORT.

  /** Applied filters parsed from a URL query, as the detail route supplies. */
  function filtersFor(query: Record<string, string>): AppliedFilters {
    return parseDocumentQuery(query)
  }

  it('scans the filter set, not the whole archive, when a filter is active', async () => {
    listMock.mockResolvedValue(page([30, 20, 10], 3))
    const filters = ref(filtersFor({ q: 'rekening' }))
    const { prevId, nextId } = useDocumentNeighbors(ref(20), filters)
    await flushPromises()

    // The observable outcome that matters: the request actually carried the
    // filter. Asserting only prev/next would pass against an unfiltered scan
    // that happened to return the same ids.
    expect(listMock).toHaveBeenCalledWith(expect.objectContaining({ q: 'rekening' }))
    expect(prevId.value).toBe(10)
    expect(nextId.value).toBe(30)
  })

  it('falls back to the unfiltered scan when the route carries no filter', async () => {
    // A cold deep-link or a shared URL: the old whole-archive behaviour, with
    // no special-casing — empty filters simply read as "not filtered".
    listMock.mockResolvedValue(page([30, 20, 10], 3))
    const { prevId, nextId } = useDocumentNeighbors(ref(20), ref(filtersFor({})))
    await flushPromises()

    const sent = listMock.mock.calls.at(-1)?.[0] ?? {}
    expect(sent).not.toHaveProperty('q')
    expect(sent).toMatchObject({ sort: 'added_date', direction: 'desc' })
    expect(prevId.value).toBe(10)
    expect(nextId.value).toBe(30)
  })

  it('reports position and total within a single-page filter set', async () => {
    // The motivating case in #140: "a search returning three documents should
    // let you page through those three". Position counts id-ASCENDING so that
    // Next (higher id) increases it.
    listMock.mockResolvedValue(page([30, 20, 10], 3))
    const { position, total, inSet } = useDocumentNeighbors(
      ref(20),
      ref(filtersFor({ q: 'rekening' })),
    )
    await flushPromises()
    expect(inSet.value).toBe(true)
    expect(position.value).toBe(2)
    expect(total.value).toBe(3)
  })

  it('states no position when there is no filter, however small the archive', async () => {
    // "Document 2 of 3" for the whole archive is noise, not orientation.
    listMock.mockResolvedValue(page([30, 20, 10], 3))
    const { position, total, inSet } = useDocumentNeighbors(ref(20))
    await flushPromises()
    expect(position.value).toBeNull()
    expect(total.value).toBeNull()
    expect(inSet.value).toBeNull()
  })

  it('reports the current document as out of the set without losing navigation', async () => {
    // Relabelling from the detail page is the workflow filtered navigation
    // exists for, so a document dropping out of its own filter set is expected
    // rather than exceptional. #140 asks specifically that this not be silent:
    // keep walking the set, but do not claim a position within it.
    listMock.mockResolvedValue(page([30, 10], 2))
    const { prevId, nextId, inSet, position } = useDocumentNeighbors(
      ref(20), // not among the returned ids
      ref(filtersFor({ facet: 'category:software' })),
    )
    await flushPromises()
    expect(inSet.value).toBe(false)
    expect(position.value).toBeNull()
    // Navigation still works — it does not strand the user.
    expect(prevId.value).toBe(10)
    expect(nextId.value).toBe(30)
  })

  it('omits a position for a filter set larger than one page', async () => {
    // Counting a multi-page set would cost round-trips the neighbour scan does
    // not otherwise need. Neighbours still follow the filter; only the
    // indicator is withheld, rather than being guessed at.
    listMock.mockResolvedValue(page(Array.from({ length: 100 }, (_, i) => 300 - i), 250))
    const { position, total, nextId } = useDocumentNeighbors(
      ref(250),
      ref(filtersFor({ q: 'invoice' })),
    )
    await flushPromises()
    expect(position.value).toBeNull()
    expect(total.value).toBeNull()
    expect(nextId.value).toBe(251)
  })

  it('re-scans when the filter changes, not only when the id does', async () => {
    listMock.mockResolvedValue(page([30, 20, 10], 3))
    const filters = ref(filtersFor({ q: 'first' }))
    const { nextId } = useDocumentNeighbors(ref(20), filters)
    await flushPromises()
    expect(nextId.value).toBe(30)

    listMock.mockResolvedValue(page([40, 20], 2))
    filters.value = filtersFor({ q: 'second' })
    await flushPromises()
    expect(listMock).toHaveBeenLastCalledWith(expect.objectContaining({ q: 'second' }))
    expect(nextId.value).toBe(40)
  })

  it('keeps the early exit while filtered', async () => {
    // A filter is a WHERE, not an ORDER BY, so a filtered scan is still
    // id-descending and crossing below the current id still settles both
    // neighbours. If this regressed, the scan would page through the whole set
    // on every detail view.
    listMock.mockResolvedValue(page(Array.from({ length: 100 }, (_, i) => 300 - i), 500))
    const { prevId } = useDocumentNeighbors(ref(250), ref(filtersFor({ q: 'invoice' })))
    await flushPromises()
    expect(prevId.value).toBe(249)
    expect(listMock).toHaveBeenCalledTimes(1) // stopped after the first page
  })
})
