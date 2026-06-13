import { afterEach, describe, expect, it, vi } from 'vitest'
import { DASHBOARD_FIELDS, getSettings, updateSettings } from '../settings'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('settings api', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('GET /api/settings returns the field list', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ dashboard_fields: ['kind'] }))
    vi.stubGlobal('fetch', fetchMock)
    expect(await getSettings()).toEqual({ dashboard_fields: ['kind'] })
    expect(String(fetchMock.mock.calls[0]![0])).toBe('/api/settings')
  })

  it('PUT /api/settings sends the field list', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ dashboard_fields: ['tags'] }))
    vi.stubGlobal('fetch', fetchMock)
    const result = await updateSettings({ dashboard_fields: ['tags'] })
    expect(result).toEqual({ dashboard_fields: ['tags'] })
    const [, init] = fetchMock.mock.calls[0]!
    expect(String(fetchMock.mock.calls[0]![0])).toBe('/api/settings')
    expect(init.method).toBe('PUT')
    expect(JSON.parse(init.body)).toEqual({ dashboard_fields: ['tags'] })
  })

  it('DASHBOARD_FIELDS contains all 8 canonical field values in order', () => {
    expect(DASHBOARD_FIELDS.map((f) => f.value)).toEqual([
      'kind', 'sender', 'tags', 'date', 'language', 'status', 'amount', 'file_type',
    ])
  })
})
