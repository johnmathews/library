import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMatter, deleteMatter, listMatters, updateMatter } from '../matters'

describe('matters API', () => {
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

  it('GETs /api/matters and returns the option list', async () => {
    respondWith([{ slug: 'acme-merger', name: 'Acme merger', document_count: 6 }])
    const matters = await listMatters()
    expect(String(fetchMock.mock.calls[0]![0])).toBe('/api/matters')
    expect(matters).toEqual([{ slug: 'acme-merger', name: 'Acme merger', document_count: 6 }])
  })

  it('GETs /api/matters?include_archived when asked', async () => {
    respondWith([])
    await listMatters(true)
    expect(String(fetchMock.mock.calls[0]![0])).toBe('/api/matters?include_archived=true')
  })

  it('POSTs /api/matters with the name/hint and returns the created matter', async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({ slug: 'acme-merger', name: 'Acme merger', document_count: 0 }),
        { status: 201, headers: { 'Content-Type': 'application/json' } },
      ),
    )
    const matter = await createMatter('Acme merger', 'The Acme acquisition')
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/matters')
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toEqual({
      name: 'Acme merger',
      hint: 'The Acme acquisition',
    })
    expect(matter).toMatchObject({ slug: 'acme-merger', name: 'Acme merger' })
  })

  it('PATCHes /api/matters/{slug} with the provided fields', async () => {
    respondWith({ slug: 'acme-merger', name: 'Merger', document_count: 0 })
    await updateMatter('acme-merger', { name: 'Merger', archived: true })
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/matters/acme-merger')
    expect(init.method).toBe('PATCH')
    expect(JSON.parse(String(init.body))).toEqual({ name: 'Merger', archived: true })
  })

  it('DELETEs /api/matters/{slug}', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }))
    await deleteMatter('acme-merger')
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/matters/acme-merger')
    expect(init.method).toBe('DELETE')
  })
})
