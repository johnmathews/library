import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  createRecipient,
  createSender,
  createUser,
  deleteKind,
  deleteRecipient,
  deleteSender,
  deleteUser,
  getArchitecture,
  getCoverage,
  getSystemInfo,
  listCurrencies,
  listFxRates,
  listUsers,
  normalizeCurrency,
  renameKind,
  renameRecipient,
  renameSender,
  seedFxRate,
  updateUser,
} from '../admin'

describe('admin API', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
    document.cookie = 'library_csrftoken=csrf-xyz'
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    document.cookie = 'library_csrftoken=; expires=Thu, 01 Jan 1970 00:00:00 GMT'
  })

  function respondWith(body: unknown, status = 200): void {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
  }

  it('getSystemInfo GETs /api/admin/system', async () => {
    const payload = {
      version: '1.2.3',
      git_sha: 'abc123',
      deployment: [{ name: 'library-webserver', role: 'web' }],
      config: { debug: false },
      stats: {
        documents_total: 10,
        documents_deleted: 1,
        documents_by_status: { indexed: 9 },
        users_total: 2,
        users_active: 2,
        jobs_total: 5,
        jobs_active: 0,
        extraction_cost_usd_total: 1.5,
        documents_budget_skipped: 0,
      },
    }
    respondWith(payload)
    const result = await getSystemInfo()
    expect(String(fetchMock.mock.calls[0]![0])).toBe('/api/admin/system')
    expect(result).toEqual(payload)
  })

  it('getArchitecture GETs /api/admin/architecture', async () => {
    respondWith({ docs: [{ name: 'overview', title: 'Overview', markdown: '# Hi' }] })
    const result = await getArchitecture()
    expect(String(fetchMock.mock.calls[0]![0])).toBe('/api/admin/architecture')
    expect(result.docs[0]!.title).toBe('Overview')
  })

  it('getCoverage GETs /api/admin/coverage', async () => {
    respondWith({
      available: true,
      backend: { pct: 95.2, threshold: 85 },
      frontend: { pct: 88, threshold: 85 },
      generated_at: '2026-06-28T00:00:00Z',
      git_sha: 'deadbeef',
    })
    const result = await getCoverage()
    expect(String(fetchMock.mock.calls[0]![0])).toBe('/api/admin/coverage')
    expect(result.available).toBe(true)
  })

  it('listUsers GETs /api/admin/users', async () => {
    respondWith([
      {
        id: 1,
        username: 'root',
        display_name: 'Root',
        is_admin: true,
        is_active: true,
        created_at: '2026-01-01T00:00:00Z',
      },
    ])
    const users = await listUsers()
    expect(String(fetchMock.mock.calls[0]![0])).toBe('/api/admin/users')
    expect(users).toHaveLength(1)
  })

  it('createUser POSTs /api/admin/users with the body and CSRF header', async () => {
    respondWith(
      {
        id: 2,
        username: 'newbie',
        display_name: 'New Bie',
        is_admin: false,
        is_active: true,
        created_at: '2026-06-28T00:00:00Z',
      },
      201,
    )
    const created = await createUser({
      username: 'newbie',
      password: 'hunter2',
      display_name: 'New Bie',
      is_admin: false,
    })
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/admin/users')
    expect(init.method).toBe('POST')
    expect((init.headers as Headers).get('X-CSRF-Token')).toBe('csrf-xyz')
    expect(JSON.parse(String(init.body))).toEqual({
      username: 'newbie',
      password: 'hunter2',
      display_name: 'New Bie',
      is_admin: false,
    })
    expect(created.username).toBe('newbie')
  })

  it('updateUser PATCHes /api/admin/users/{id} with the flag body', async () => {
    respondWith({
      id: 3,
      username: 'mod',
      display_name: 'Mod',
      is_admin: true,
      is_active: true,
      created_at: '2026-06-28T00:00:00Z',
    })
    const updated = await updateUser(3, { is_admin: true })
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/admin/users/3')
    expect(init.method).toBe('PATCH')
    expect(JSON.parse(String(init.body))).toEqual({ is_admin: true })
    expect(updated.is_admin).toBe(true)
  })

  it('surfaces a 409 from updateUser as an ApiError with the detail', async () => {
    respondWith({ detail: 'cannot remove the last active admin' }, 409)
    await expect(updateUser(1, { is_admin: false })).rejects.toMatchObject({
      status: 409,
      detail: 'cannot remove the last active admin',
    })
  })

  it('deleteUser DELETEs /api/admin/users/{id} with the CSRF header', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }))
    await deleteUser(7)
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/admin/users/7')
    expect(init.method).toBe('DELETE')
    expect((init.headers as Headers).get('X-CSRF-Token')).toBe('csrf-xyz')
  })

  it('surfaces a 400 from deleteUser as an ApiError with the detail', async () => {
    respondWith({ detail: 'cannot delete your own account' }, 400)
    await expect(deleteUser(1)).rejects.toMatchObject({
      status: 400,
      detail: 'cannot delete your own account',
    })
  })

  describe('recipients', () => {
    it('createRecipient POSTs /api/admin/recipients with the name body', async () => {
      respondWith({ id: 5, name: 'Alice' }, 201)
      const created = await createRecipient('Alice')
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toBe('/api/admin/recipients')
      expect(init.method).toBe('POST')
      expect((init.headers as Headers).get('X-CSRF-Token')).toBe('csrf-xyz')
      expect(JSON.parse(String(init.body))).toEqual({ name: 'Alice' })
      expect(created).toEqual({ id: 5, name: 'Alice' })
    })

    it('renameRecipient PATCHes with just {name} when merge is false', async () => {
      respondWith({ id: 5, name: 'Alicia' })
      await renameRecipient(5, 'Alicia')
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toBe('/api/admin/recipients/5')
      expect(init.method).toBe('PATCH')
      expect(JSON.parse(String(init.body))).toEqual({ name: 'Alicia' })
    })

    it('renameRecipient PATCHes with {name, merge:true} when merge is true', async () => {
      respondWith({ id: 9, name: 'Alicia' })
      await renameRecipient(5, 'Alicia', true)
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toBe('/api/admin/recipients/5')
      expect(init.method).toBe('PATCH')
      expect(JSON.parse(String(init.body))).toEqual({ name: 'Alicia', merge: true })
    })

    it('surfaces a 409 rename collision as an ApiError with the conflict body', async () => {
      respondWith(
        {
          detail: 'a recipient named "Alicia" already exists',
          target_id: 9,
          target_name: 'Alicia',
          target_document_count: 3,
        },
        409,
      )
      await expect(renameRecipient(5, 'Alicia')).rejects.toMatchObject({
        status: 409,
        detail: 'a recipient named "Alicia" already exists',
        body: { target_id: 9, target_name: 'Alicia', target_document_count: 3 },
      })
    })

    it('deleteRecipient omits reassign_to entirely when reassignTo is undefined', async () => {
      fetchMock.mockResolvedValue(new Response(null, { status: 204 }))
      await deleteRecipient(5)
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(String(url)).toBe('/api/admin/recipients/5')
      expect(init.method).toBe('DELETE')
      expect((init.headers as Headers).get('X-CSRF-Token')).toBe('csrf-xyz')
    })

    it('deleteRecipient sends an empty reassign_to when reassignTo is null', async () => {
      fetchMock.mockResolvedValue(new Response(null, { status: 204 }))
      await deleteRecipient(5, null)
      expect(String(fetchMock.mock.calls[0]![0])).toBe('/api/admin/recipients/5?reassign_to=')
    })

    it('deleteRecipient sends reassign_to=<id> when reassignTo is a number', async () => {
      fetchMock.mockResolvedValue(new Response(null, { status: 204 }))
      await deleteRecipient(5, 12)
      expect(String(fetchMock.mock.calls[0]![0])).toBe('/api/admin/recipients/5?reassign_to=12')
    })
  })

  describe('senders', () => {
    it('createSender POSTs /api/admin/senders with the name body', async () => {
      respondWith({ id: 3, name: 'Acme' }, 201)
      const created = await createSender('Acme')
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toBe('/api/admin/senders')
      expect(init.method).toBe('POST')
      expect(JSON.parse(String(init.body))).toEqual({ name: 'Acme' })
      expect(created).toEqual({ id: 3, name: 'Acme' })
    })

    it('renameSender PATCHes with just {name} when merge is false', async () => {
      respondWith({ id: 3, name: 'Acme Inc' })
      await renameSender(3, 'Acme Inc')
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toBe('/api/admin/senders/3')
      expect(init.method).toBe('PATCH')
      expect(JSON.parse(String(init.body))).toEqual({ name: 'Acme Inc' })
    })

    it('renameSender PATCHes with {name, merge:true} when merge is true', async () => {
      respondWith({ id: 7, name: 'Acme Inc' })
      await renameSender(3, 'Acme Inc', true)
      const init = fetchMock.mock.calls[0]![1] as RequestInit
      expect(JSON.parse(String(init.body))).toEqual({ name: 'Acme Inc', merge: true })
    })

    it('deleteSender omits reassign_to when reassignTo is undefined', async () => {
      fetchMock.mockResolvedValue(new Response(null, { status: 204 }))
      await deleteSender(3)
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(String(url)).toBe('/api/admin/senders/3')
      expect(init.method).toBe('DELETE')
    })

    it('deleteSender sends an empty reassign_to when reassignTo is null', async () => {
      fetchMock.mockResolvedValue(new Response(null, { status: 204 }))
      await deleteSender(3, null)
      expect(String(fetchMock.mock.calls[0]![0])).toBe('/api/admin/senders/3?reassign_to=')
    })

    it('deleteSender sends reassign_to=<id> when reassignTo is a number', async () => {
      fetchMock.mockResolvedValue(new Response(null, { status: 204 }))
      await deleteSender(3, 8)
      expect(String(fetchMock.mock.calls[0]![0])).toBe('/api/admin/senders/3?reassign_to=8')
    })
  })

  describe('kinds', () => {
    it('renameKind PATCHes /api/admin/kinds/{slug} with the name body', async () => {
      respondWith({ slug: 'invoice', name: 'Invoices' })
      const renamed = await renameKind('invoice', 'Invoices')
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toBe('/api/admin/kinds/invoice')
      expect(init.method).toBe('PATCH')
      expect(JSON.parse(String(init.body))).toEqual({ name: 'Invoices' })
      expect(renamed).toEqual({ slug: 'invoice', name: 'Invoices' })
    })

    it('deleteKind omits reassign_to when reassignTo is undefined', async () => {
      fetchMock.mockResolvedValue(new Response(null, { status: 204 }))
      await deleteKind('invoice')
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(String(url)).toBe('/api/admin/kinds/invoice')
      expect(init.method).toBe('DELETE')
    })

    it('deleteKind sends an empty reassign_to when reassignTo is null', async () => {
      fetchMock.mockResolvedValue(new Response(null, { status: 204 }))
      await deleteKind('invoice', null)
      expect(String(fetchMock.mock.calls[0]![0])).toBe('/api/admin/kinds/invoice?reassign_to=')
    })

    it('deleteKind sends reassign_to=<slug> when reassignTo is a slug', async () => {
      fetchMock.mockResolvedValue(new Response(null, { status: 204 }))
      await deleteKind('invoice', 'receipt')
      expect(String(fetchMock.mock.calls[0]![0])).toBe(
        '/api/admin/kinds/invoice?reassign_to=receipt',
      )
    })
  })

  describe('currencies', () => {
    it('listCurrencies GETs /api/admin/currencies', async () => {
      respondWith([{ code: 'USD', document_count: 12 }])
      const result = await listCurrencies()
      expect(String(fetchMock.mock.calls[0]![0])).toBe('/api/admin/currencies')
      expect(result).toEqual([{ code: 'USD', document_count: 12 }])
    })

    it('normalizeCurrency POSTs the from_code/to_code body', async () => {
      respondWith({
        from_code: 'usd',
        to_code: 'USD',
        counts: { documents: 4 },
        fx_rate_missing: false,
      })
      const result = await normalizeCurrency('usd', 'USD')
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toBe('/api/admin/currencies/normalize')
      expect(init.method).toBe('POST')
      expect(JSON.parse(String(init.body))).toEqual({ from_code: 'usd', to_code: 'USD' })
      expect(result.to_code).toBe('USD')
    })

    it('surfaces a 409 override conflict from normalizeCurrency', async () => {
      respondWith(
        {
          detail: 'rename would collide with user overrides',
          conflicts: [{ table: 'series_overrides', sender_id: 2, kind_id: null }],
        },
        409,
      )
      await expect(normalizeCurrency('usd', 'USD')).rejects.toMatchObject({
        status: 409,
        detail: 'rename would collide with user overrides',
      })
    })
  })

  describe('fx rates', () => {
    it('listFxRates GETs /api/admin/fx-rates', async () => {
      respondWith([
        {
          code: 'EUR',
          document_count: 3,
          is_base: false,
          has_rate: true,
          rate_to_base: '1.08',
          as_of: '2026-07-01',
        },
      ])
      const result = await listFxRates()
      expect(String(fetchMock.mock.calls[0]![0])).toBe('/api/admin/fx-rates')
      expect(result[0]!.code).toBe('EUR')
    })

    it('seedFxRate omits rate_to_base when source is live', async () => {
      respondWith({ currency: 'EUR', as_of: '2026-07-04', rate_to_base: '1.08' })
      await seedFxRate({ currency: 'EUR', source: 'live' })
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toBe('/api/admin/fx-rates')
      expect(init.method).toBe('POST')
      expect(JSON.parse(String(init.body))).toEqual({ currency: 'EUR', source: 'live' })
    })

    it('seedFxRate includes rate_to_base when source is manual with a rate', async () => {
      respondWith({ currency: 'EUR', as_of: '2026-07-04', rate_to_base: '1.10' })
      await seedFxRate({ currency: 'EUR', source: 'manual', rateToBase: '1.10' })
      const init = fetchMock.mock.calls[0]![1] as RequestInit
      expect(JSON.parse(String(init.body))).toEqual({
        currency: 'EUR',
        source: 'manual',
        rate_to_base: '1.10',
      })
    })
  })
})
