import { afterEach, describe, expect, it, vi } from 'vitest'
import { DASHBOARD_FIELDS, TILE_PREVIEWS, getSettings, updateAppearance, updateSettings } from '../settings'

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

  it('PUT /api/settings/appearance sends both tone and tile preview', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({ dashboard_fields: ['kind'], background_tone: 'slate', tile_preview: 'whole_page' }),
      )
    vi.stubGlobal('fetch', fetchMock)
    const result = await updateAppearance('slate', 'whole_page')
    expect(result.tile_preview).toBe('whole_page')
    const [url, init] = fetchMock.mock.calls[0]!
    expect(String(url)).toBe('/api/settings/appearance')
    expect(init.method).toBe('PUT')
    expect(JSON.parse(init.body)).toEqual({ background_tone: 'slate', tile_preview: 'whole_page' })
  })

  it('TILE_PREVIEWS contains full_width and whole_page in order', () => {
    expect(TILE_PREVIEWS.map((m) => m.value)).toEqual(['full_width', 'whole_page'])
  })

  it('DASHBOARD_FIELDS contains all 8 canonical field values in order', () => {
    expect(DASHBOARD_FIELDS.map((f) => f.value)).toEqual([
      'kind', 'sender', 'tags', 'date', 'language', 'status', 'amount', 'file_type',
    ])
  })
})
