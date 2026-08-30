import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchFacetCounts } from '../facets'

describe('facets API', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
  })

  afterEach(() => vi.unstubAllGlobals())

  function respondWith(body: unknown, status = 200): void {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
  }

  const COUNT = {
    facet_key: 'category',
    value_key: 'software',
    documents: 12,
    first_date: '2026-01-01',
    last_date: '2026-06-30',
  }

  it('GETs /api/facets/counts and unwraps the counts envelope', async () => {
    respondWith({ counts: [COUNT] })
    const counts = await fetchFacetCounts()
    expect(String(fetchMock.mock.calls[0]![0])).toBe('/api/facets/counts')
    expect(counts).toEqual([COUNT])
  })
})
