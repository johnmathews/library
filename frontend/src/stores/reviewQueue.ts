/**
 * Step-through review queue (W6).
 *
 * Holds an ordered list of document ids that need review plus a cursor, so the
 * user can fix + verify flagged documents one after another without bouncing
 * back to the dashboard between each. The detail page drives this store in
 * "queue mode" (`?queue=1`): it navigates by `currentId`, drops resolved
 * documents with `resolveCurrent()`, and skips/steps with `next()`/`prev()`.
 *
 * The store holds only ids (the detail page loads each document itself), so it
 * survives navigation within the session. Editing is per-field autosave on the
 * detail page (already revalidated server-side, W1), so there is no separate
 * "save" here — a document leaves the queue when its flag clears or it is
 * verified.
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { listDocuments } from '@/api/documents'

export const useReviewQueueStore = defineStore('reviewQueue', () => {
  const ids = ref<number[]>([])
  const index = ref(0)

  const isActive = computed(() => ids.value.length > 0)
  const currentId = computed<number | null>(() => ids.value[index.value] ?? null)
  const total = computed(() => ids.value.length)
  /** 1-based position of the current document, for "X of N" display. */
  const position = computed(() => (ids.value.length ? index.value + 1 : 0))
  const hasPrev = computed(() => index.value > 0)
  const hasNext = computed(() => index.value < ids.value.length - 1)

  /** Load the current needs-review set and point the cursor at the first one.
   *  Returns the first document's id, or null if nothing needs review.
   *  `limit` is capped at the list API's maximum (100) — a larger value is a
   *  422, which is what silently broke the queue entry point in e2e; re-entering
   *  the queue after clearing the first batch reloads the next. */
  async function start(limit = 100): Promise<number | null> {
    const response = await listDocuments({ review_status: 'needs_review', limit, offset: 0 })
    ids.value = response.items.map((item) => item.id)
    index.value = 0
    return currentId.value
  }

  function clampIndex(): void {
    index.value = ids.value.length ? Math.min(index.value, ids.value.length - 1) : 0
  }

  /** Advance the cursor (keeps the current document in the queue for a later
   *  pass — used by "Skip"). Returns the new current id, or null at the end. */
  function next(): number | null {
    if (index.value < ids.value.length - 1) {
      index.value += 1
      return currentId.value
    }
    return null
  }

  /** Step back to the previous document. */
  function prev(): number | null {
    if (index.value > 0) index.value -= 1
    return currentId.value
  }

  /** Drop the current document (its flag cleared or it was verified) and land on
   *  what is now at the same slot — the next document, or null when the queue is
   *  empty. */
  function resolveCurrent(): number | null {
    if (!ids.value.length) return null
    ids.value.splice(index.value, 1)
    clampIndex()
    return currentId.value
  }

  function reset(): void {
    ids.value = []
    index.value = 0
  }

  return {
    ids,
    index,
    isActive,
    currentId,
    total,
    position,
    hasPrev,
    hasNext,
    start,
    next,
    prev,
    resolveCurrent,
    reset,
  }
})
