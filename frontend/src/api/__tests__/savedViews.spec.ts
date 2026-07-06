import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  createSavedView,
  deleteSavedView,
  listSavedViews,
  reorderSavedViews,
  updateSavedView,
} from '../savedViews'

describe('saved-views API', () => {
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

  const VIEW = {
    id: 1,
    name: 'Unpaid invoices',
    filter_state: { kind: 'invoice' },
    pinned: false,
    sort_order: 0,
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
  }

  it('GETs /api/saved-views and returns the list', async () => {
    respondWith([VIEW])
    const views = await listSavedViews()
    expect(String(fetchMock.mock.calls[0]![0])).toBe('/api/saved-views')
    expect(views).toEqual([VIEW])
  })

  it('POSTs /api/saved-views with the name/filter_state/pinned', async () => {
    respondWith({ ...VIEW, pinned: true }, 201)
    const created = await createSavedView({
      name: 'Unpaid invoices',
      filter_state: { kind: 'invoice' },
      pinned: true,
    })
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/saved-views')
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toEqual({
      name: 'Unpaid invoices',
      filter_state: { kind: 'invoice' },
      pinned: true,
    })
    expect(created).toMatchObject({ id: 1, pinned: true })
  })

  it('PATCHes /api/saved-views/{id} with the provided fields', async () => {
    respondWith({ ...VIEW, name: 'Renamed' })
    await updateSavedView(1, { name: 'Renamed' })
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/saved-views/1')
    expect(init.method).toBe('PATCH')
    expect(JSON.parse(String(init.body))).toEqual({ name: 'Renamed' })
  })

  it('DELETEs /api/saved-views/{id}', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }))
    await deleteSavedView(1)
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/saved-views/1')
    expect(init.method).toBe('DELETE')
  })

  it('POSTs /api/saved-views/reorder with the id list', async () => {
    respondWith([VIEW])
    await reorderSavedViews([3, 1, 2])
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/saved-views/reorder')
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toEqual({ ids: [3, 1, 2] })
  })
})
