/**
 * Held-emails store (hold-for-review queue).
 *
 * `count` powers the dashboard "N emails held" affordance; it is refreshed
 * from the dashboard's list-load path (never onMounted in the persistent
 * layout — auth-guard timing) with a cheap total-only probe, mirroring the
 * needs-review count. `items` back the /held-emails view for the current
 * `status` filter.
 *
 * Ingest-anyway is asynchronous on the backend (a 202 that queues the
 * override task), so `ingest()` marks the row queued and polls the detail
 * until the row resolves (leaves `held`) or reports a `last_error`; the view
 * renders the queued state and watches the row leave the held filter. Dismiss
 * is synchronous (DB-only) and applies immediately.
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  dismissHeldEmail,
  getHeldEmail,
  ingestHeldEmail,
  listHeldEmails,
  HELD_EMAILS_MAX_LIMIT,
  type HeldEmailDetail,
  type HeldEmailItem,
  type HeldEmailListStatus,
} from '@/api/heldEmails'
import { ApiError } from '@/api/client'

/** How often the post-ingest poll re-fetches the row's detail. */
export const INGEST_POLL_INTERVAL_MS = 2000
/** Give the override task ~30s before falling back to a plain list reload. */
export const INGEST_POLL_MAX_ATTEMPTS = 15

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

export const useHeldEmailsStore = defineStore('heldEmails', () => {
  /** Open (held) rows, independent of the current filter — the dashboard badge. */
  const count = ref(0)
  const items = ref<HeldEmailItem[]>([])
  const total = ref(0)
  const status = ref<HeldEmailListStatus>('held')
  const loading = ref(false)
  const loadError = ref<string | null>(null)
  /** Last ingest/dismiss failure, for a banner near the actions. */
  const actionError = ref<string | null>(null)
  /** Per-row in-flight action (the POST itself), keyed by row id. */
  const acting = ref<Record<number, 'ingest' | 'dismiss'>>({})
  /** Rows whose override task is queued and being polled for resolution. */
  const queuedIds = ref<Set<number>>(new Set())

  let generation = 0

  /** Cheap total-only probe of the open queue (limit=1). Non-critical: keeps
   *  the last known count on failure, like the needs-review probe. */
  async function refreshCount(): Promise<void> {
    try {
      const response = await listHeldEmails({ status: 'held', limit: 1, offset: 0 })
      count.value = response.total
    } catch {
      // Non-critical: keep the last known count if the probe fails.
    }
  }

  /** Load the given filter (defaults to the current one). A newer load wins. */
  async function load(nextStatus: HeldEmailListStatus = status.value): Promise<void> {
    const gen = ++generation
    status.value = nextStatus
    loading.value = true
    loadError.value = null
    try {
      const response = await listHeldEmails({
        status: nextStatus,
        limit: HELD_EMAILS_MAX_LIMIT,
        offset: 0,
      })
      if (gen !== generation) return
      items.value = response.items
      total.value = response.total
      // The held list IS the open queue, so its total doubles as the count.
      if (nextStatus === 'held') count.value = response.total
    } catch {
      if (gen !== generation) return
      loadError.value = 'Sorry, the held emails could not be loaded. Try again later.'
    } finally {
      if (gen === generation) loading.value = false
    }
  }

  /** Fold a resolved detail back into the list: rows leave the `held` filter,
   *  and update in place under a filter that still shows them. */
  function applyResolved(detail: HeldEmailDetail): void {
    const wasListed = items.value.some((row) => row.id === detail.id)
    if (status.value === 'held') {
      if (wasListed) {
        items.value = items.value.filter((row) => row.id !== detail.id)
        total.value = Math.max(0, total.value - 1)
      }
      count.value = Math.max(0, count.value - 1)
    } else if (status.value === 'all' || status.value === detail.status) {
      if (wasListed) {
        items.value = items.value.map((row) => (row.id === detail.id ? detail : row))
      }
      count.value = Math.max(0, count.value - 1)
    }
  }

  /** Replace a still-held row with fresher fields (e.g. a new last_error). */
  function updateRow(detail: HeldEmailDetail): void {
    items.value = items.value.map((row) => (row.id === detail.id ? detail : row))
  }

  /** Poll the row's detail until the override resolves it (status leaves
   *  `held`), it reports a failure (`last_error`), or attempts run out. */
  async function pollResolution(id: number): Promise<void> {
    for (let attempt = 0; attempt < INGEST_POLL_MAX_ATTEMPTS; attempt++) {
      await sleep(INGEST_POLL_INTERVAL_MS)
      let detail: HeldEmailDetail
      try {
        detail = await getHeldEmail(id)
      } catch {
        continue // transient fetch failure: try again on the next tick
      }
      if (detail.status !== 'held') {
        queuedIds.value.delete(id)
        applyResolved(detail)
        void refreshCount()
        return
      }
      if (detail.last_error) {
        // The override failed (e.g. message not found on re-fetch); the row
        // stays held — surface the error line and stop polling.
        queuedIds.value.delete(id)
        updateRow(detail)
        return
      }
    }
    queuedIds.value.delete(id)
    void load(status.value)
  }

  /** Queue the ingest-anyway override for a held row. The row enters the
   *  queued state immediately; resolution arrives via the poll. */
  async function ingest(id: number): Promise<void> {
    if (acting.value[id] || queuedIds.value.has(id)) return
    acting.value[id] = 'ingest'
    actionError.value = null
    try {
      await ingestHeldEmail(id)
      queuedIds.value.add(id)
      void pollResolution(id)
    } catch (error: unknown) {
      actionError.value =
        error instanceof ApiError ? error.detail : 'Sorry, the ingest could not be queued.'
    } finally {
      delete acting.value[id]
    }
  }

  /** Dismiss a held row (DB-only, immediate). */
  async function dismiss(id: number): Promise<void> {
    if (acting.value[id]) return
    acting.value[id] = 'dismiss'
    actionError.value = null
    try {
      const detail = await dismissHeldEmail(id)
      applyResolved(detail)
    } catch (error: unknown) {
      actionError.value =
        error instanceof ApiError ? error.detail : 'Sorry, the email could not be dismissed.'
    } finally {
      delete acting.value[id]
    }
  }

  return {
    count,
    items,
    total,
    status,
    loading,
    loadError,
    actionError,
    acting,
    queuedIds,
    refreshCount,
    load,
    ingest,
    dismiss,
  }
})
