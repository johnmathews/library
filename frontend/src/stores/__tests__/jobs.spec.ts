import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useJobsStore, type DocumentEvent } from '../jobs'
import { useNotificationsStore } from '../notifications'
import { listJobs } from '@/api/documents'

vi.mock('@/api/documents', () => ({
  listJobs: vi.fn(() => Promise.resolve([])),
}))

const listJobsMock = vi.mocked(listJobs)

class MockEventSource {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSED = 2
  static instances: MockEventSource[] = []

  readyState = MockEventSource.CONNECTING
  closed = false
  private listeners: Record<string, ((event: unknown) => void)[]> = {}

  constructor(public url: string) {
    MockEventSource.instances.push(this)
  }

  addEventListener(type: string, fn: (event: unknown) => void): void {
    ;(this.listeners[type] ??= []).push(fn)
  }

  emit(type: string, payload?: unknown): void {
    for (const fn of this.listeners[type] ?? []) fn(payload)
  }

  emitDocument(data: DocumentEvent): void {
    this.emit('document', { data: JSON.stringify(data) })
  }

  close(): void {
    this.closed = true
    this.readyState = MockEventSource.CLOSED
  }
}

function event(partial: Partial<DocumentEvent> & { document_id: number; status: string }): DocumentEvent {
  return { event: 'status_changed', title: null, ...partial }
}

describe('useJobsStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    MockEventSource.instances = []
    listJobsMock.mockReset()
    listJobsMock.mockResolvedValue([])
    vi.stubGlobal('EventSource', MockEventSource)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it('tracks in-flight documents in activeCount', () => {
    const jobs = useJobsStore()
    jobs.handle(event({ document_id: 1, status: 'ocr', title: 'A' }))
    jobs.handle(event({ document_id: 2, status: 'extract', title: 'B' }))
    expect(jobs.activeCount).toBe(2)

    jobs.handle(event({ document_id: 1, status: 'embed', title: 'A' }))
    expect(jobs.activeCount).toBe(2) // still in flight, just a later stage
  })

  it('raises a success toast and clears the doc when indexed', () => {
    const jobs = useJobsStore()
    const notifications = useNotificationsStore()
    jobs.handle(event({ document_id: 5, status: 'ocr', title: 'Invoice' }))
    jobs.handle(event({ document_id: 5, status: 'indexed', title: 'Invoice' }))

    expect(jobs.activeCount).toBe(0)
    expect(notifications.toasts).toHaveLength(1)
    expect(notifications.toasts[0]).toMatchObject({
      variant: 'success',
      title: 'Document processed',
      to: '/documents/5',
    })
  })

  it('raises an error toast on failure', () => {
    const jobs = useJobsStore()
    const notifications = useNotificationsStore()
    jobs.handle(event({ document_id: 9, status: 'ocr', title: 'Bad' }))
    jobs.handle(event({ document_id: 9, event: 'failed', status: 'failed', title: 'Bad' }))

    expect(jobs.activeCount).toBe(0)
    expect(notifications.toasts[0]).toMatchObject({ variant: 'error', to: '/documents/9' })
  })

  it('toasts at most once per document for a terminal state', () => {
    const jobs = useJobsStore()
    const notifications = useNotificationsStore()
    jobs.handle(event({ document_id: 3, status: 'indexed', title: 'X' }))
    jobs.handle(event({ document_id: 3, status: 'indexed', title: 'X' }))
    expect(notifications.toasts).toHaveLength(1)
  })

  it('connects to /api/events and applies streamed events', () => {
    const jobs = useJobsStore()
    jobs.connect()
    expect(MockEventSource.instances).toHaveLength(1)
    const source = MockEventSource.instances[0]!
    expect(source.url).toBe('/api/events')

    source.emitDocument(event({ document_id: 11, status: 'ocr', title: 'Streamed' }))
    expect(jobs.activeCount).toBe(1)
    expect(jobs.activeList[0]).toMatchObject({ id: 11, status: 'ocr' })
  })

  it('seeds the active set from the jobs snapshot on connect', async () => {
    listJobsMock.mockResolvedValue([
      {
        id: 1,
        status: 'doing',
        task_name: 'library.jobs.process_document',
        attempts: 0,
        scheduled_at: null,
        started_at: null,
        finished_at: null,
        document_id: 42,
        active: true,
        document_title: 'Snapshot',
        document_status: 'ocr',
        error: null,
        cost_usd: null,
        tokens: null,
      },
      {
        id: 2,
        status: 'succeeded',
        task_name: 'library.jobs.process_document',
        attempts: 0,
        scheduled_at: null,
        started_at: null,
        finished_at: null,
        document_id: 7,
        active: false,
        document_title: 'Done',
        document_status: 'indexed',
        error: null,
        cost_usd: null,
        tokens: null,
      },
    ])
    const jobs = useJobsStore()
    jobs.connect()
    await vi.waitFor(() => expect(jobs.activeCount).toBe(1))
    expect(jobs.activeList[0]).toMatchObject({ id: 42, status: 'ocr', title: 'Snapshot' })
  })

  it('reconnects with backoff after the stream closes', () => {
    vi.useFakeTimers()
    const jobs = useJobsStore()
    jobs.connect()
    const first = MockEventSource.instances[0]!

    first.readyState = MockEventSource.CLOSED
    first.emit('error')
    expect(first.closed).toBe(true)
    expect(MockEventSource.instances).toHaveLength(1)

    vi.advanceTimersByTime(1000)
    expect(MockEventSource.instances).toHaveLength(2)
  })

  it('disconnect closes the stream', () => {
    const jobs = useJobsStore()
    jobs.connect()
    const source = MockEventSource.instances[0]!
    jobs.disconnect()
    expect(source.closed).toBe(true)
  })
})
