import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createKind, listKinds, listSenders, listTags } from '../taxonomy'
import { ApiError } from '../client'

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

  it('POSTs /api/kinds with the name and returns the created kind', async () => {
    respondWith({ slug: 'quote', name: 'Quote' })
    const kind = await createKind('Quote')
    const [url, init] = fetchMock.mock.calls[0]!
    expect(String(url)).toBe('/api/kinds')
    expect((init as RequestInit).method).toBe('POST')
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({ name: 'Quote' })
    expect(kind).toEqual({ slug: 'quote', name: 'Quote' })
  })

  it('throws an ApiError carrying the conflict body on a near-duplicate (409)', async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: "a similar kind named 'Quote' already exists",
          existing_slug: 'quote',
          existing_name: 'Quote',
        }),
        { status: 409, headers: { 'Content-Type': 'application/json' } },
      ),
    )
    await expect(createKind('Quotes')).rejects.toMatchObject({
      status: 409,
      body: { existing_slug: 'quote', existing_name: 'Quote' },
    })
    await expect(createKind('Quotes')).rejects.toBeInstanceOf(ApiError)
  })
})
