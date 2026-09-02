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
 * ## Following the active filter (#140)
 *
 * Neighbours are drawn from the **active filter set** when the detail route
 * carries one, so paging out of a three-result search walks those three rather
 * than jumping to unrelated documents in the whole archive.
 *
 * That is a different thing from following the list's SORT, which was tried and
 * deliberately rejected (see above) — and the reason it was rejected does not
 * apply here. The complaint was that "Next" moved in a counter-intuitive
 * *direction*; this changes only which documents are in the *set*. Order within
 * the set stays id-ascending, exactly as before.
 *
 * With no filter on the route — a cold deep-link, a refresh, a shared URL — it
 * falls back to the unfiltered whole-archive scan, so the old behaviour is
 * still what a context-free visit gets.
 *
 * ## How the scan works
 *
 * There is no server neighbour endpoint and no `id` sort on `GET /api/documents`,
 * so neighbours are computed client-side. We scan sorted by `added_date desc` —
 * which returns documents in effectively id-descending order, since `created_at`
 * and the autoincrement id are both assigned at insert — and read off the
 * nearest ids either side of the current one **numerically** (not by list
 * position), so the result is correct even if two documents share an
 * `added_date` and tie out of strict id order.
 *
 * The scan paginates (the list endpoint caps `limit` at 100) and stops as soon
 * as it crosses below the current id: in a descending scan every later page
 * holds only smaller ids, so once any id below the current one appears both
 * neighbours are settled. **That early exit survives filtering**: a filter is a
 * `WHERE`, not an `ORDER BY`, so a filtered scan is still id-descending and the
 * same reasoning holds. A hard page cap bounds the cost for a pathological
 * library; beyond it the neighbours degrade to `null` rather than looping.
 */
import { ref, watch, type Ref } from 'vue'
import { listDocuments } from '@/api/documents'
import { hasActiveFilters, toDocumentFilters, type AppliedFilters } from '@/utils/documentQuery'

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
  /**
   * 1-based position of the current document within the active filter set, and
   * that set's size — for the "2 of 3" indicator. Both null whenever a position
   * cannot be stated honestly:
   *
   *  - no filter is active (the set is the whole archive; a position in it is
   *    not information the reader wants);
   *  - the set is larger than one page, so counting it would cost extra
   *    round-trips the neighbour scan itself does not need;
   *  - the current document is NOT in the set (see `inSet`).
   */
  position: Ref<number | null>
  total: Ref<number | null>
  /**
   * Whether the current document is itself part of the active filter set.
   * False is a real and expected state, not an error: relabelling a document
   * from this very page is the workflow filtered navigation exists for, so a
   * document can drop out of the set the user is walking. Navigation continues
   * within the set either way — the caller is expected to SAY that the set no
   * longer contains this document rather than silently pretend otherwise.
   * Null when no filter is active.
   */
  inSet: Ref<boolean | null>
}

export function useDocumentNeighbors(
  currentId: Ref<number | null>,
  filters?: Ref<AppliedFilters | null>,
): DocumentNeighbors {
  const prevId = ref<number | null>(null)
  const nextId = ref<number | null>(null)
  const loading = ref(false)
  const position = ref<number | null>(null)
  const total = ref<number | null>(null)
  const inSet = ref<boolean | null>(null)
  // Bumped on every id/filter change so a scan started for stale inputs can
  // detect it has been superseded and bail without clobbering the newer result.
  let generation = 0

  async function compute(id: number, gen: number): Promise<void> {
    const applied = filters?.value ?? null
    const filtered = applied !== null && hasActiveFilters(applied)

    // Nearest ids either side of `id`, tracked numerically as pages stream in.
    let prev: number | null = null // largest id strictly below `id`
    let next: number | null = null // smallest id strictly above `id`
    // Only meaningful in filtered mode; see `position` above.
    const seenIds: number[] = []
    let setTotal: number | null = null
    let offset = 0
    try {
      for (let pageIdx = 0; pageIdx < MAX_PAGES; pageIdx++) {
        const window = { limit: PAGE_SIZE, offset }
        const resp = await listDocuments(
          filtered && applied
            ? toDocumentFilters(applied, window)
            : { sort: 'added_date', direction: 'desc', ...window },
        )
        if (gen !== generation) return // newer inputs superseded this scan
        if (setTotal === null) setTotal = resp.total
        let sawBelow = false
        for (const item of resp.items) {
          if (filtered) seenIds.push(item.id)
          if (item.id < id) {
            sawBelow = true
            if (prev === null || item.id > prev) prev = item.id
          } else if (item.id > id) {
            if (next === null || item.id < next) next = item.id
          }
        }
        // Descending scan: everything on later pages is smaller, so once any id
        // below the current one appears, `next` is final and `prev` cannot grow.
        // A filter is a WHERE, not an ORDER BY, so this holds when filtered too.
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
        position.value = null
        total.value = null
        inSet.value = null
      }
      return
    }
    prevId.value = prev
    nextId.value = next

    if (!filtered) {
      // The whole archive is not a "set" the reader is walking; saying
      // "document 41 of 263" would be noise, not orientation.
      position.value = null
      total.value = null
      inSet.value = null
      return
    }

    inSet.value = seenIds.includes(id)
    // Only state a position when the whole set was actually enumerated. The
    // early exit above means a multi-page set was NOT fully scanned, and
    // counting it would cost round-trips that navigation itself does not need.
    // The motivating case — "a search returning three documents" — is one page.
    const fullyScanned = setTotal !== null && setTotal <= PAGE_SIZE && seenIds.length === setTotal
    if (fullyScanned && inSet.value) {
      // Position in the order the user steps through: id ascending, so that
      // "Next" (higher id) increases it.
      position.value = seenIds.filter((seen) => seen <= id).length
      total.value = setTotal
    } else {
      position.value = null
      total.value = fullyScanned ? setTotal : null
    }
  }

  watch(
    // Re-scan when the id changes OR the filter does — navigating within a
    // filtered set changes the id, but editing the filter changes the set.
    [currentId, () => filters?.value ?? null] as const,
    ([id]) => {
      const gen = ++generation
      prevId.value = null
      nextId.value = null
      position.value = null
      total.value = null
      inSet.value = null
      if (id == null || Number.isNaN(id)) {
        loading.value = false
        return
      }
      loading.value = true
      void compute(id, gen).finally(() => {
        if (gen === generation) loading.value = false
      })
    },
    { immediate: true, deep: true },
  )

  return { prevId, nextId, loading, position, total, inSet }
}
