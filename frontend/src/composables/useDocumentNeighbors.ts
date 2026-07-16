/**
 * Previous/next document neighbours for the detail view (`/documents/:id`).
 *
 * There is no server neighbour endpoint and the list view keeps its results in
 * component-local state, so neighbours are computed client-side: we replay the
 * user's *remembered* sort order (localStorage `library:doc-sort-v1`, the same
 * key the list view writes) against `GET /api/documents`, **unfiltered**, and
 * read off the ids either side of the current one. This is self-contained — it
 * works on a cold deep-link or refresh, independent of how the user arrived.
 *
 * The scan paginates (the list endpoint caps `limit` at 100) and stops as soon
 * as the current id is located with a following id, or the list is exhausted.
 * A hard page cap bounds the cost for a pathological library; beyond it the
 * neighbours degrade to `null` rather than looping unbounded.
 */
import { ref, watch, type Ref } from 'vue'
import { useStorage } from '@vueuse/core'
import { listDocuments } from '@/api/documents'
import { DEFAULT_SORT, DEFAULT_SORT_DIRECTION, type SortPreference } from '@/utils/documentQuery'

/** Page size for the neighbour scan (the list endpoint's max `limit`). */
const PAGE_SIZE = 100
/** Cap the scan at 20 pages (2000 documents) so it can never loop unbounded. */
const MAX_PAGES = 20

export interface DocumentNeighbors {
  /** Id of the document before the current one, or null at the start / unknown. */
  prevId: Ref<number | null>
  /** Id of the document after the current one, or null at the end / unknown. */
  nextId: Ref<number | null>
  /** True while a scan is in flight. */
  loading: Ref<boolean>
}

export function useDocumentNeighbors(currentId: Ref<number | null>): DocumentNeighbors {
  const sortPref = useStorage<SortPreference>('library:doc-sort-v1', {
    sort: DEFAULT_SORT,
    dir: DEFAULT_SORT_DIRECTION,
  })
  const prevId = ref<number | null>(null)
  const nextId = ref<number | null>(null)
  const loading = ref(false)
  // Bumped on every id change so a scan started for an old id can detect it has
  // been superseded and bail without clobbering the newer result.
  let generation = 0

  async function compute(id: number, gen: number): Promise<void> {
    const ids: number[] = []
    let offset = 0
    let cappedOut = false
    try {
      for (let pageIdx = 0; pageIdx < MAX_PAGES; pageIdx++) {
        const resp = await listDocuments({
          sort: sortPref.value.sort,
          direction: sortPref.value.dir,
          limit: PAGE_SIZE,
          offset,
        })
        if (gen !== generation) return // a newer id superseded this scan
        for (const item of resp.items) ids.push(item.id)
        const idx = ids.indexOf(id)
        // Done once the current id is located AND has a following id...
        if (idx !== -1 && idx < ids.length - 1) break
        // ...or the list is exhausted (a short page means no more documents).
        if (resp.items.length < PAGE_SIZE) break
        offset += PAGE_SIZE
        if (pageIdx === MAX_PAGES - 1) cappedOut = true
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
    const idx = ids.indexOf(id)
    if (cappedOut && idx === -1) {
      console.warn(
        'useDocumentNeighbors: document list exceeds the neighbour scan cap; prev/next unavailable',
      )
    }
    prevId.value = idx > 0 ? (ids[idx - 1] ?? null) : null
    nextId.value = idx !== -1 && idx < ids.length - 1 ? (ids[idx + 1] ?? null) : null
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
