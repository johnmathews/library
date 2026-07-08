import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { askQuestion, listThreads, getThread, deleteThread, renameThread } from '../ask'
import { ApiError } from '../client'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('askQuestion', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
    document.cookie = 'library_csrftoken=csrf-123'
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    document.cookie = 'library_csrftoken=; expires=Thu, 01 Jan 1970 00:00:00 GMT'
  })

  it('POSTs the question to /api/ask with the CSRF header and returns the answer', async () => {
    const body = {
      answer: 'Two invoices are due this month.',
      citations: [{ document_id: 7, title: 'Energy bill' }],
      used_tools: ['search'],
      cost_usd: 0.0123,
      thread_id: 5,
    }
    fetchMock.mockResolvedValue(jsonResponse(body))

    const result = await askQuestion('which invoices are due?')

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/ask')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string)).toEqual({ question: 'which invoices are due?' })
    const headers = new Headers(init.headers)
    expect(headers.get('X-CSRF-Token')).toBe('csrf-123')
    expect(headers.get('Content-Type')).toBe('application/json')
    expect(result).toEqual(body)
    expect(result.thread_id).toBe(5)
  })

  it('rejects a 503 with an ApiError carrying the backend detail', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: 'no API key configured' }, 503))
    const promise = askQuestion('anything')
    await expect(promise).rejects.toBeInstanceOf(ApiError)
    await expect(promise).rejects.toMatchObject({ status: 503, detail: 'no API key configured' })
  })

  it('forwards an AbortSignal to fetch', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ answer: '', citations: [], used_tools: [], cost_usd: 0, thread_id: 1 }),
    )
    const controller = new AbortController()
    await askQuestion('hi', undefined, controller.signal)
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(init.signal).toBe(controller.signal)
  })

  it('includes thread_id in the POST body when continuing a thread', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ answer: 'a', citations: [], used_tools: [], cost_usd: 0, thread_id: 5 }),
    )
    await askQuestion('follow up?', 5)
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(JSON.parse(init.body as string)).toEqual({ question: 'follow up?', thread_id: 5 })
  })

  it('lists, gets, and deletes threads', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse([{ id: 1, title: 'T', created_at: '', updated_at: '', turn_count: 2, total_cost_usd: 0.01 }]))
    const threads = await listThreads()
    expect(threads[0]!.turn_count).toBe(2)

    fetchMock.mockResolvedValueOnce(jsonResponse({ id: 1, title: 'T', turns: [] }))
    const detail = await getThread(1)
    expect(detail.id).toBe(1)

    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }))
    await deleteThread(1)
    const [url, init] = fetchMock.mock.calls[2] as [string, RequestInit]
    expect(url).toBe('/api/ask/threads/1')
    expect(init.method).toBe('DELETE')
  })

  it('PATCHes a new title when renaming a thread', async () => {
    const updated = { id: 1, title: 'Utility costs', created_at: '', updated_at: '', turn_count: 3, total_cost_usd: 0.05 }
    fetchMock.mockResolvedValueOnce(jsonResponse(updated))
    const result = await renameThread(1, 'Utility costs')
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/ask/threads/1')
    expect(init.method).toBe('PATCH')
    expect(JSON.parse(init.body as string)).toEqual({ title: 'Utility costs' })
    expect(result.title).toBe('Utility costs')
  })
})
