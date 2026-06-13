import { afterEach, describe, expect, it, vi } from 'vitest'
import { getSettings, updateSettings } from '../settings'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('settings api', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('GET /api/settings returns the field list', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ dashboard_fields: ['kind'] })))
    expect(await getSettings()).toEqual({ dashboard_fields: ['kind'] })
  })

  it('PUT /api/settings sends the field list', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ dashboard_fields: ['tags'] }))
    vi.stubGlobal('fetch', fetchMock)
    const result = await updateSettings({ dashboard_fields: ['tags'] })
    expect(result).toEqual({ dashboard_fields: ['tags'] })
    const [, init] = fetchMock.mock.calls[0]
    expect(init.method).toBe('PUT')
    expect(JSON.parse(init.body)).toEqual({ dashboard_fields: ['tags'] })
  })
})
