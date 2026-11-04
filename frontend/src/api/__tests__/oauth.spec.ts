import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../client'
import {
  approveAuthorizationRequest,
  denyAuthorizationRequest,
  getAuthorizationRequest,
  listOAuthGrants,
  revokeOAuthGrant,
} from '../oauth'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const REQUEST = {
  client_id: 'abc',
  client_name: 'Claude',
  redirect_uri: 'https://claude.ai/api/mcp/auth_callback',
  redirect_host: 'claude.ai',
  scopes: ['library'],
  expires_at: '2026-11-04T19:00:00Z',
  expired: false,
}

describe('oauth api', () => {
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

  it('GET /api/oauth/requests/{id} reads the pending request, without a CSRF header', async () => {
    fetchMock.mockResolvedValue(jsonResponse(REQUEST))
    expect(await getAuthorizationRequest('req-1')).toEqual(REQUEST)
    const [url, init] = fetchMock.mock.calls[0]!
    expect(String(url)).toBe('/api/oauth/requests/req-1')
    expect(init.method).toBe('GET')
    expect((init.headers as Headers).get('X-CSRF-Token')).toBeNull()
  })

  it('encodes the request id into the path', async () => {
    fetchMock.mockResolvedValue(jsonResponse(REQUEST))
    await getAuthorizationRequest('a/b?c')
    expect(String(fetchMock.mock.calls[0]![0])).toBe('/api/oauth/requests/a%2Fb%3Fc')
  })

  it('POST …/approve sends the CSRF header and returns the redirect', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ redirect_to: 'https://claude.ai/cb?code=x' }))
    expect(await approveAuthorizationRequest('req-1')).toEqual({
      redirect_to: 'https://claude.ai/cb?code=x',
    })
    const [url, init] = fetchMock.mock.calls[0]!
    expect(String(url)).toBe('/api/oauth/requests/req-1/approve')
    expect(init.method).toBe('POST')
    expect((init.headers as Headers).get('X-CSRF-Token')).toBe('csrf-123')
    expect(init.body).toBeUndefined()
  })

  it('POST …/deny sends the CSRF header and returns the redirect', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ redirect_to: 'https://claude.ai/cb?error=access_denied' }),
    )
    expect(await denyAuthorizationRequest('req-1')).toEqual({
      redirect_to: 'https://claude.ai/cb?error=access_denied',
    })
    const [url, init] = fetchMock.mock.calls[0]!
    expect(String(url)).toBe('/api/oauth/requests/req-1/deny')
    expect(init.method).toBe('POST')
    expect((init.headers as Headers).get('X-CSRF-Token')).toBe('csrf-123')
  })

  it('rejects with the API status and detail (e.g. 410 expired)', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: 'this authorization request expired' }, 410))
    const error = await approveAuthorizationRequest('req-1').catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(410)
    expect((error as ApiError).detail).toBe('this authorization request expired')
  })

  it('GET /api/oauth/grants lists connected apps', async () => {
    const grants = [
      {
        id: 3,
        client_id: 'abc',
        client_name: 'Claude',
        created_at: '2026-11-01T10:00:00Z',
        last_used_at: null,
      },
    ]
    fetchMock.mockResolvedValue(jsonResponse(grants))
    expect(await listOAuthGrants()).toEqual(grants)
    const [url, init] = fetchMock.mock.calls[0]!
    expect(String(url)).toBe('/api/oauth/grants')
    expect(init.method).toBe('GET')
  })

  it('DELETE /api/oauth/grants/{id} sends the CSRF header and resolves on 204', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }))
    await expect(revokeOAuthGrant(3)).resolves.toBeUndefined()
    const [url, init] = fetchMock.mock.calls[0]!
    expect(String(url)).toBe('/api/oauth/grants/3')
    expect(init.method).toBe('DELETE')
    expect((init.headers as Headers).get('X-CSRF-Token')).toBe('csrf-123')
  })
})
