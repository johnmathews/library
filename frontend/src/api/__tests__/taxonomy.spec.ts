import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { listKinds, listSenders, listTags } from '../taxonomy'

describe('taxonomy API', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
  })

  afterEach(() => vi.unstubAllGlobals())

  function respondWith(body: unknown): void {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
  }

  it('GETs /api/kinds and returns the option list', async () => {
    respondWith([{ slug: 'invoice', name: 'Invoice', document_count: 3 }])
    const kinds = await listKinds()
    expect(String(fetchMock.mock.calls[0]![0])).toBe('/api/kinds')
    expect(kinds).toEqual([{ slug: 'invoice', name: 'Invoice', document_count: 3 }])
  })

  it('GETs /api/senders', async () => {
    respondWith([{ id: 3, name: 'Eneco', document_count: 7 }])
    const senders = await listSenders()
    expect(String(fetchMock.mock.calls[0]![0])).toBe('/api/senders')
    expect(senders[0]).toMatchObject({ id: 3, name: 'Eneco' })
  })

  it('GETs /api/tags', async () => {
    respondWith([{ slug: 'energie', name: 'Energie', document_count: 2 }])
    const tags = await listTags()
    expect(String(fetchMock.mock.calls[0]![0])).toBe('/api/tags')
    expect(tags[0]).toMatchObject({ slug: 'energie' })
  })
})
