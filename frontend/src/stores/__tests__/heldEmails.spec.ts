import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useHeldEmailsStore, INGEST_POLL_INTERVAL_MS } from '../heldEmails'
import { listHeldEmails, type HeldEmailDetail, type HeldEmailItem } from '@/api/heldEmails'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function makeHeld(overrides: Partial<HeldEmailItem> = {}): HeldEmailItem {
  return {
    id: 5,
    message_id: '<msg-5@example.com>',
    sender: 'billing@acme.example',
    subject: 'Your May invoice',
    received_at: '2026-07-10T08:00:00Z',
    created_at: '2026-07-10T08:01:00Z',
    verdict: 'llm_hold',
    reason: 'Looks like a newsletter',
    status: 'held',
    owner_id: 1,
    owner: 'John',
    resolved_at: null,
    document_ids: [],
    last_error: null,
    ...overrides,
  }
}

function makeDetail(overrides: Partial<HeldEmailDetail> = {}): HeldEmailDetail {
  return { ...makeHeld(), trace: {}, ...overrides }
}

function listBody(items: HeldEmailItem[], total = items.length) {
  return { items, total, limit: 100, offset: 0 }
}

describe('held-emails API client', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
    fetchMock.mockResolvedValue(jsonResponse(listBody([])))
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('caps limit at 100 in the built query string (the API 422s above it)', async () => {
    await listHeldEmails({ status: 'held', limit: 500, offset: 0 })
    const url = new URL(String(fetchMock.mock.calls[0]![0]), 'http://localhost')
    expect(url.pathname).toBe('/api/held-emails')
    expect(Number(url.searchParams.get('limit'))).toBe(100)
  })

  it('always sends a limit within the API cap', async () => {
    await listHeldEmails({ status: 'all', limit: 25, offset: 50 })
    const url = new URL(String(fetchMock.mock.calls[0]![0]), 'http://localhost')
    expect(url.searchParams.get('status')).toBe('all')
    expect(url.searchParams.get('offset')).toBe('50')
    expect(Number(url.searchParams.get('limit'))).toBeLessThanOrEqual(100)
  })
})

describe('useHeldEmailsStore', () => {
  const fetchMock = vi.fn()

  /** URLs of every /api/held-emails request made so far. */
  function heldEmailUrls(): URL[] {
    return fetchMock.mock.calls
      .map((call) => new URL(String(call[0]), 'http://localhost'))
      .filter((url) => url.pathname.startsWith('/api/held-emails'))
  }

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it('refreshCount() probes with limit=1 and reads only the total', async () => {
    fetchMock.mockResolvedValue(jsonResponse(listBody([makeHeld()], 7)))
    const store = useHeldEmailsStore()

    await store.refreshCount()

    expect(store.count).toBe(7)
    // The probe is total-only: limit=1, status=held.
    const url = heldEmailUrls()[0]!
    expect(url.searchParams.get('limit')).toBe('1')
    expect(url.searchParams.get('status')).toBe('held')
    // It must not touch the loaded items.
    expect(store.items).toEqual([])
  })

  it('refreshCount() swallows errors and keeps the last known count', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(listBody([], 3)))
    const store = useHeldEmailsStore()
    await store.refreshCount()
    expect(store.count).toBe(3)

    fetchMock.mockRejectedValueOnce(new Error('network down'))
    await store.refreshCount()
    expect(store.count).toBe(3)
  })

  it('load() fetches the filter with a capped limit and syncs count for held', async () => {
    const rows = [makeHeld({ id: 1 }), makeHeld({ id: 2 })]
    fetchMock.mockResolvedValue(jsonResponse(listBody(rows, 2)))
    const store = useHeldEmailsStore()

    await store.load('held')

    expect(store.items.map((row) => row.id)).toEqual([1, 2])
    expect(store.total).toBe(2)
    expect(store.count).toBe(2)
    expect(store.loading).toBe(false)
    const url = heldEmailUrls()[0]!
    expect(url.searchParams.get('status')).toBe('held')
    expect(Number(url.searchParams.get('limit'))).toBeLessThanOrEqual(100)
  })

  it('load() of a resolved filter never rewrites the held count', async () => {
    fetchMock.mockResolvedValue(jsonResponse(listBody([makeHeld({ status: 'dismissed' })], 1)))
    const store = useHeldEmailsStore()
    store.count = 4

    await store.load('dismissed')

    expect(store.count).toBe(4)
    expect(store.items).toHaveLength(1)
  })

  it('load() failure sets loadError and clears loading', async () => {
    fetchMock.mockRejectedValue(new Error('boom'))
    const store = useHeldEmailsStore()

    await store.load('held')

    expect(store.loadError).toBeTruthy()
    expect(store.loading).toBe(false)
  })

  it('ingest() queues the row, then a successful poll removes it from the held list', async () => {
    vi.useFakeTimers()
    let detailStatus = 'held'
    fetchMock.mockImplementation((input: unknown, init?: RequestInit) => {
      const url = new URL(String(input), 'http://localhost')
      if (url.pathname === '/api/held-emails/5/ingest' && init?.method === 'POST') {
        return Promise.resolve(jsonResponse({ queued: true, job_id: 99 }, 202))
      }
      if (url.pathname === '/api/held-emails/5') {
        return Promise.resolve(
          jsonResponse(
            makeDetail({
              status: detailStatus as HeldEmailDetail['status'],
              document_ids: detailStatus === 'ingested' ? [42] : [],
            }),
          ),
        )
      }
      return Promise.resolve(jsonResponse(listBody([], 0)))
    })

    const store = useHeldEmailsStore()
    store.items = [makeHeld({ id: 5 })]
    store.total = 1
    store.count = 1

    await store.ingest(5)

    // Observable outcome 1: the row is in the queued state (the button flips).
    expect(store.queuedIds.has(5)).toBe(true)
    expect(store.acting[5]).toBeUndefined() // the POST finished

    // Next poll tick finds it resolved → the row leaves the held list.
    detailStatus = 'ingested'
    await vi.advanceTimersByTimeAsync(INGEST_POLL_INTERVAL_MS + 10)

    expect(store.queuedIds.has(5)).toBe(false)
    expect(store.items).toHaveLength(0)
    expect(store.total).toBe(0)
    expect(store.count).toBe(0)
    // A refetch of the detail happened (that IS the refresh the user perceives).
    const detailCalls = heldEmailUrls().filter((url) => url.pathname === '/api/held-emails/5')
    expect(detailCalls.length).toBeGreaterThanOrEqual(1)
  })

  it('a poll that finds last_error stops queuing and surfaces the error on the row', async () => {
    vi.useFakeTimers()
    fetchMock.mockImplementation((input: unknown, init?: RequestInit) => {
      const url = new URL(String(input), 'http://localhost')
      if (url.pathname === '/api/held-emails/5/ingest' && init?.method === 'POST') {
        return Promise.resolve(jsonResponse({ queued: true, job_id: 99 }, 202))
      }
      if (url.pathname === '/api/held-emails/5') {
        return Promise.resolve(
          jsonResponse(makeDetail({ status: 'held', last_error: 'message not found in Held' })),
        )
      }
      return Promise.resolve(jsonResponse(listBody([], 0)))
    })

    const store = useHeldEmailsStore()
    store.items = [makeHeld({ id: 5 })]
    store.total = 1
    store.count = 1

    await store.ingest(5)
    await vi.advanceTimersByTimeAsync(INGEST_POLL_INTERVAL_MS + 10)

    expect(store.queuedIds.has(5)).toBe(false)
    // The row stays held but now carries the error line the user can read.
    expect(store.items[0]!.last_error).toBe('message not found in Held')
  })

  it('ingest() failure (409 already resolved) sets actionError and never queues', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: 'held email 5 is already dismissed' }, 409))
    const store = useHeldEmailsStore()
    store.items = [makeHeld({ id: 5 })]

    await store.ingest(5)

    expect(store.queuedIds.has(5)).toBe(false)
    expect(store.actionError).toContain('already dismissed')
  })

  it('dismiss() removes the row from the held filter and decrements the count', async () => {
    fetchMock.mockImplementation((input: unknown, init?: RequestInit) => {
      const url = new URL(String(input), 'http://localhost')
      if (url.pathname === '/api/held-emails/5/dismiss' && init?.method === 'POST') {
        return Promise.resolve(
          jsonResponse(makeDetail({ status: 'dismissed', resolved_at: '2026-07-14T10:00:00Z' })),
        )
      }
      return Promise.resolve(jsonResponse(listBody([], 0)))
    })
    const store = useHeldEmailsStore()
    store.items = [makeHeld({ id: 5 }), makeHeld({ id: 6 })]
    store.total = 2
    store.count = 2

    await store.dismiss(5)

    expect(store.items.map((row) => row.id)).toEqual([6])
    expect(store.total).toBe(1)
    expect(store.count).toBe(1)
  })

  it('dismiss() under the all filter updates the row in place instead of removing it', async () => {
    fetchMock.mockImplementation((input: unknown, init?: RequestInit) => {
      const url = new URL(String(input), 'http://localhost')
      if (url.pathname === '/api/held-emails/5/dismiss' && init?.method === 'POST') {
        return Promise.resolve(jsonResponse(makeDetail({ status: 'dismissed' })))
      }
      return Promise.resolve(jsonResponse(listBody([makeHeld({ id: 5 })], 1)))
    })
    const store = useHeldEmailsStore()
    await store.load('all')

    await store.dismiss(5)

    expect(store.items).toHaveLength(1)
    expect(store.items[0]!.status).toBe('dismissed')
  })
})
