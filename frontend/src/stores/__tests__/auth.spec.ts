import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useAuthStore, type User } from '../auth'

const me: User = {
  id: 1,
  username: 'anna',
  display_name: 'Anna',
  is_admin: false,
  preferences: { dashboard_fields: [] },
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('useAuthStore', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
    document.cookie = 'library_csrftoken=csrf-abc'
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    document.cookie = 'library_csrftoken=; expires=Thu, 01 Jan 1970 00:00:00 GMT'
  })

  it('login POSTs credentials with the CSRF header and stores the user', async () => {
    fetchMock.mockResolvedValue(jsonResponse(me))
    const auth = useAuthStore()

    const result = await auth.login('anna', 'hunter2')

    expect(result).toEqual(me)
    expect(auth.user).toEqual(me)
    expect(auth.isAuthenticated).toBe(true)

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/auth/login')
    expect(init.method).toBe('POST')
    expect((init.headers as Headers).get('X-CSRF-Token')).toBe('csrf-abc')
    expect(init.body).toBe(JSON.stringify({ username: 'anna', password: 'hunter2' }))
  })

  it('login failure leaves the store unauthenticated', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: 'invalid credentials' }, 401))
    const auth = useAuthStore()

    await expect(auth.login('anna', 'wrong')).rejects.toMatchObject({ status: 401 })
    expect(auth.user).toBeNull()
  })

  it('ensureLoaded calls /api/auth/me once and caches the result', async () => {
    fetchMock.mockResolvedValue(jsonResponse(me))
    const auth = useAuthStore()

    const [first, second] = await Promise.all([auth.ensureLoaded(), auth.ensureLoaded()])
    await auth.ensureLoaded()

    expect(first).toEqual(me)
    expect(second).toEqual(me)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0]![0]).toBe('/api/auth/me')
  })

  it('ensureLoaded resolves null on 401 without throwing', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: 'not authenticated' }, 401))
    const auth = useAuthStore()

    await expect(auth.ensureLoaded()).resolves.toBeNull()
    expect(auth.isAuthenticated).toBe(false)
  })

  it('logout POSTs with CSRF and clears the user', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(me))
    const auth = useAuthStore()
    await auth.login('anna', 'hunter2')

    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }))
    await auth.logout()

    expect(auth.user).toBeNull()
    const [url, init] = fetchMock.mock.calls[1] as [string, RequestInit]
    expect(url).toBe('/api/auth/logout')
    expect((init.headers as Headers).get('X-CSRF-Token')).toBe('csrf-abc')
    // and the cached "me" now resolves null without another request
    await expect(auth.ensureLoaded()).resolves.toBeNull()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('exposes dashboardFields from the loaded user', async () => {
    const meWithFields: User = {
      id: 1,
      username: 'anna',
      display_name: 'Anna',
      is_admin: false,
      preferences: { dashboard_fields: ['kind', 'tags'] },
    }
    fetchMock.mockResolvedValue(jsonResponse(meWithFields))
    const store = useAuthStore()

    await store.ensureLoaded()

    expect(store.dashboardFields).toEqual(['kind', 'tags'])
  })

  it('isAdmin is false when no user is loaded', () => {
    const store = useAuthStore()
    expect(store.isAdmin).toBe(false)
  })

  it('isAdmin reflects the loaded user flag', async () => {
    const admin: User = {
      id: 2,
      username: 'root',
      display_name: 'Root',
      is_admin: true,
      preferences: { dashboard_fields: [] },
    }
    fetchMock.mockResolvedValue(jsonResponse(admin))
    const store = useAuthStore()

    await store.ensureLoaded()

    expect(store.isAdmin).toBe(true)
  })

  it('applyPreferences updates the field set', async () => {
    fetchMock.mockResolvedValue(jsonResponse(me))
    const store = useAuthStore()
    await store.ensureLoaded()

    store.applyPreferences({ dashboard_fields: ['amount'] })

    expect(store.dashboardFields).toEqual(['amount'])
  })

  it('exposes dockPosition from preferences, defaulting to top-right', async () => {
    fetchMock.mockResolvedValue(jsonResponse(me))
    const store = useAuthStore()
    await store.ensureLoaded()

    expect(store.dockPosition).toBe('top-right')

    store.applyPreferences({ ...me.preferences, dock_position: 'bottom-left' })

    expect(store.dockPosition).toBe('bottom-left')
  })

  it('phoneColumns defaults to 2 when the preference is absent', () => {
    const auth = useAuthStore()
    auth.user = {
      id: 1,
      username: 'u',
      display_name: 'U',
      is_admin: false,
      preferences: { dashboard_fields: [] },
    }
    expect(auth.phoneColumns).toBe(2)
  })

  it('phoneColumns reflects the stored preference', () => {
    const auth = useAuthStore()
    auth.user = {
      id: 1,
      username: 'u',
      display_name: 'U',
      is_admin: false,
      preferences: { dashboard_fields: [], phone_columns: 3 },
    }
    expect(auth.phoneColumns).toBe(3)
  })
})
