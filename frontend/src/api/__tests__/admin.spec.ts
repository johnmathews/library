import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  createUser,
  getArchitecture,
  getCoverage,
  getSystemInfo,
  listUsers,
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
})
