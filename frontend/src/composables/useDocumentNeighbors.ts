/**
 * Previous/next document neighbours for the detail view (`/documents/:id`).
 *
 * Navigation is **by document id**: "Next document" goes to the next-higher id
 * (N+1), "Previous document" to the next-lower id (N-1). This is independent of
 * whatever sort the list view is currently using — stepping through documents in
 * id order is what reads as intuitive (a document's number is its position in
 * the sequence you added things), which the list-sort-following behaviour was
 * not (in the default newest-first view it sent "Next" to an *older*, lower id).
 *
 * There is no server neighbour endpoint and no `id` sort on `GET /api/documents`,
 * so neighbours are computed client-side. We scan the list **unfiltered** sorted
 * by `added_date desc` — which returns documents in effectively id-descending
 * order, since `created_at` and the autoincrement id are both assigned at insert
 * — and read off the nearest ids either side of the current one **numerically**
 * (not by list position), so the result is correct even if two documents share
 * an `added_date` and tie out of strict id order. This is self-contained: it
 * works on a cold deep-link or refresh, independent of how the user arrived.
 *
 * The scan paginates (the list endpoint caps `limit` at 100) and stops as soon
 * as it crosses below the current id: in a descending scan every later page
 * holds only smaller ids, so once any id below the current one appears both
 * neighbours are settled. A hard page cap bounds the cost for a pathological
 * library; beyond it the neighbours degrade to `null` rather than looping.
 */
import { ref, watch, type Ref } from 'vue'
import { listDocuments } from '@/api/documents'

/** Page size for the neighbour scan (the list endpoint's max `limit`). */
const PAGE_SIZE = 100
/** Cap the scan at 20 pages (2000 documents) so it can never loop unbounded. */
const MAX_PAGES = 20

export interface DocumentNeighbors {
  /** Id of the next-lower document (N-1), or null when this is the lowest id. */
  prevId: Ref<number | null>
  /** Id of the next-higher document (N+1), or null when this is the highest id. */
  nextId: Ref<number | null>
  /** True while a scan is in flight. */
  loading: Ref<boolean>
}

export function useDocumentNeighbors(currentId: Ref<number | null>): DocumentNeighbors {
  const prevId = ref<number | null>(null)
  const nextId = ref<number | null>(null)
  const loading = ref(false)
  // Bumped on every id change so a scan started for an old id can detect it has
  // been superseded and bail without clobbering the newer result.
  let generation = 0

  async function compute(id: number, gen: number): Promise<void> {
    // Nearest ids either side of `id`, tracked numerically as pages stream in.
    let prev: number | null = null // largest id strictly below `id`
    let next: number | null = null // smallest id strictly above `id`
    let offset = 0
    try {
      for (let pageIdx = 0; pageIdx < MAX_PAGES; pageIdx++) {
        const resp = await listDocuments({
          sort: 'added_date',
          direction: 'desc',
          limit: PAGE_SIZE,
          offset,
        })
        if (gen !== generation) return // a newer id superseded this scan
        let sawBelow = false
        for (const item of resp.items) {
          if (item.id < id) {
            sawBelow = true
            if (prev === null || item.id > prev) prev = item.id
          } else if (item.id > id) {
            if (next === null || item.id < next) next = item.id
          }
        }
        // Descending scan: everything on later pages is smaller, so once any id
        // below the current one appears, `next` is final and `prev` cannot grow.
        if (sawBelow) break
        // ...or the list is exhausted (a short page means no more documents).
        if (resp.items.length < PAGE_SIZE) break
        offset += PAGE_SIZE
      }
    } catch {
      // A failed list fetch just means no neighbours — degrade quietly rather
      // than surfacing an error on a page whose primary content loaded fine.
      if (gen === generation) {
        prevId.value = null
        nextId.value = null
      }
      return
    }
    prevId.value = prev
    nextId.value = next
  }

  watch(
    currentId,
    (id) => {
      const gen = ++generation
      prevId.value = null
      nextId.value = null
      if (id == null || Number.isNaN(id)) {
        loading.value = false
        return
      }
      loading.value = true
      void compute(id, gen).finally(() => {
        if (gen === generation) loading.value = false
      })
    },
    { immediate: true },
  )

  return { prevId, nextId, loading }
}
