import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { listJobs } from '@/api/documents'
import { useNotificationsStore } from './notifications'

/** Payload of one SSE `document` event (see library.api.events). */
export interface DocumentEvent {
  document_id: number
  event: string
  status: string
  title: string | null
}

/** A document currently being processed, as tracked in the navbar/indicator. */
export interface ActiveDocument {
  id: number
  status: string
  title: string | null
}

// Non-terminal pipeline stages — a document in any of these is "in flight".
const IN_FLIGHT = new Set(['received', 'ocr', 'extract', 'markdown', 'embed'])
const BASE_BACKOFF_MS = 1000
const MAX_BACKOFF_MS = 30000
const SNAPSHOT_LIMIT = 200

/**
 * Live view of document-processing jobs, fed by the `/api/events` SSE stream.
 *
 * Tracks in-flight documents (for the navbar indicator + Jobs view) and raises
 * a single success/error toast per document when it reaches a terminal state.
 * Connection is opened once at app-shell mount and survives drops via capped
 * exponential-backoff reconnect. Lifecycle toasts cover document processing
 * only — other job types still appear in the Jobs view but stay quiet.
 */
export const useJobsStore = defineStore('jobs', () => {
  const active = ref<Map<number, ActiveDocument>>(new Map())
  const connected = ref(false)
  // The most recent document event, bumped on EVERY event (not just terminal
  // ones). Views watch this to refetch/patch themselves as a document advances
  // through the pipeline — see JobsView, DocumentListView, DocumentDetailView.
  const lastEvent = ref<DocumentEvent | null>(null)

  let source: EventSource | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let backoff = BASE_BACKOFF_MS
  // Terminal events can arrive more than once (e.g. a retried job); only the
  // first per document raises a toast.
  const toastedTerminal = new Set<number>()

  const activeCount = computed(() => active.value.size)
  const activeList = computed<ActiveDocument[]>(() => [...active.value.values()])

  function setActive(id: number, doc: ActiveDocument): void {
    const next = new Map(active.value)
    next.set(id, doc)
    active.value = next
  }

  function removeActive(id: number): void {
    if (!active.value.has(id)) return
    const next = new Map(active.value)
    next.delete(id)
    active.value = next
  }

  function toastOnce(id: number, raise: () => void): void {
    if (toastedTerminal.has(id)) return
    toastedTerminal.add(id)
    raise()
  }

  /** Apply one document event to the active set and raise terminal toasts. */
  function handle(event: DocumentEvent): void {
    const notifications = useNotificationsStore()
    const { document_id: id, status, title } = event
    // Surface every event so views can react to intra-pipeline stage changes,
    // not only enter/leave of the in-flight set (which `activeCount` tracks).
    lastEvent.value = event

    if (status === 'indexed') {
      removeActive(id)
      toastOnce(id, () =>
        notifications.push({
          variant: 'success',
          title: 'Document processed',
          message: title ?? undefined,
          to: `/documents/${id}`,
        }),
      )
    } else if (status === 'failed' || event.event === 'failed') {
      removeActive(id)
      toastOnce(id, () =>
        notifications.push({
          variant: 'error',
          title: 'Processing failed',
          message: title ?? undefined,
          to: `/documents/${id}`,
        }),
      )
    } else if (IN_FLIGHT.has(status)) {
      setActive(id, { id, status, title })
    }
  }

  /** Seed the active set from the current jobs so a fresh tab isn't blank. */
  async function loadSnapshot(): Promise<void> {
    try {
      const jobs = await listJobs({ limit: SNAPSHOT_LIMIT })
      const next = new Map<number, ActiveDocument>()
      for (const job of jobs) {
        if (
          job.document_id !== null &&
          job.active &&
          job.document_status !== null &&
          IN_FLIGHT.has(job.document_status)
        ) {
          next.set(job.document_id, {
            id: job.document_id,
            status: job.document_status,
            title: job.document_title,
          })
        }
      }
      active.value = next
    } catch {
      // Best-effort: a failed snapshot just means we start empty and fill from SSE.
    }
  }

  function open(): void {
    // EventSource is absent under SSR and in the jsdom test environment; the
    // store stays inert there rather than throwing on connect.
    if (typeof EventSource === 'undefined') return
    source = new EventSource('/api/events', { withCredentials: true })
    source.addEventListener('open', () => {
      connected.value = true
      backoff = BASE_BACKOFF_MS
    })
    source.addEventListener('document', (raw) => {
      try {
        handle(JSON.parse((raw as MessageEvent).data) as DocumentEvent)
      } catch {
        // Ignore malformed payloads rather than tear the stream down.
      }
    })
    source.addEventListener('error', () => {
      connected.value = false
      // While CONNECTING, the browser is already retrying; only step in once it
      // has given up (CLOSED) — e.g. the server closed the stream.
      if (source && source.readyState === EventSource.CLOSED) {
        scheduleReconnect()
      }
    })
  }

  function scheduleReconnect(): void {
    if (reconnectTimer !== null) return
    closeSource()
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      open()
    }, backoff)
    backoff = Math.min(backoff * 2, MAX_BACKOFF_MS)
  }

  function closeSource(): void {
    if (source !== null) {
      source.close()
      source = null
    }
  }

  /** Open the stream (idempotent) and seed the snapshot. */
  function connect(): void {
    if (source !== null || reconnectTimer !== null) return
    void loadSnapshot()
    open()
  }

  /** Close the stream, cancel any pending reconnect, and reset dedup state. */
  function disconnect(): void {
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    closeSource()
    connected.value = false
    // Bound the terminal-toast dedup set to a single connected session.
    toastedTerminal.clear()
  }

  return {
    active,
    activeCount,
    activeList,
    connected,
    lastEvent,
    connect,
    disconnect,
    handle,
  }
})
