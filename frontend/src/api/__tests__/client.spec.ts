import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch, getCookie } from '../client'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function clearCookies(): void {
  for (const part of document.cookie.split(';')) {
    const name = part.split('=')[0]?.trim()
    if (name) document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT`
  }
}

describe('getCookie', () => {
  afterEach(clearCookies)

  it('reads a cookie by name', () => {
    document.cookie = 'library_csrftoken=tok-123'
    document.cookie = 'other=value'
    expect(getCookie('library_csrftoken')).toBe('tok-123')
  })

  it('returns null when absent', () => {
    expect(getCookie('missing')).toBeNull()
  })
})

describe('apiFetch', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    clearCookies()
  })

  it('sends GET requests with same-origin credentials and no CSRF header', async () => {
    document.cookie = 'library_csrftoken=tok-123'
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }))

    await apiFetch('/api/documents')

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/documents')
    expect(init.method).toBe('GET')
    expect(init.credentials).toBe('same-origin')
    expect((init.headers as Headers).get('X-CSRF-Token')).toBeNull()
  })

  it('echoes the CSRF cookie on state-changing requests', async () => {
    document.cookie = 'library_csrftoken=tok-456'
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }))

    await apiFetch('/api/documents', { method: 'POST', body: { a: 1 } })

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    const headers = init.headers as Headers
    expect(headers.get('X-CSRF-Token')).toBe('tok-456')
    expect(headers.get('Content-Type')).toBe('application/json')
    expect(init.body).toBe(JSON.stringify({ a: 1 }))
  })

  it('serialises query parameters, skipping undefined values', async () => {
    fetchMock.mockResolvedValue(jsonResponse([]))

    await apiFetch('/api/documents', { query: { q: 'belasting', page: 2, kind: undefined } })

    const [url] = fetchMock.mock.calls[0] as [string]
    expect(url).toBe('/api/documents?q=belasting&page=2')
  })

  it('normalises JSON error bodies into ApiError', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: 'invalid credentials' }, 401))

    const error = await apiFetch('/api/auth/login', { method: 'POST', body: {} }).catch(
      (e: unknown) => e,
    )

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(401)
    expect((error as ApiError).detail).toBe('invalid credentials')
  })

  it('falls back to status text for non-JSON error bodies', async () => {
    fetchMock.mockResolvedValue(
      new Response('boom', { status: 502, statusText: 'Bad Gateway' }),
    )

    const error = await apiFetch('/api/x').catch((e: unknown) => e)
    expect((error as ApiError).detail).toBe('Bad Gateway')
  })

  it('returns undefined for 204 responses', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }))
    await expect(apiFetch<void>('/api/x', { method: 'DELETE' })).resolves.toBeUndefined()
  })
})
