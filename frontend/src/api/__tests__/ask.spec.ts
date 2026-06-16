import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { askQuestion } from '../ask'
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
  })

  it('rejects a 503 with an ApiError carrying the backend detail', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: 'no API key configured' }, 503))
    const promise = askQuestion('anything')
    await expect(promise).rejects.toBeInstanceOf(ApiError)
    await expect(promise).rejects.toMatchObject({ status: 503, detail: 'no API key configured' })
  })

  it('forwards an AbortSignal to fetch', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ answer: '', citations: [], used_tools: [], cost_usd: 0 }),
    )
    const controller = new AbortController()
    await askQuestion('hi', controller.signal)
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(init.signal).toBe(controller.signal)
  })
})
