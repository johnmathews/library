import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import { authGuard, routes } from '../index'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const Stub = { template: '<div />' }

function makeRouter(): Router {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'documents', component: Stub },
      { path: '/login', name: 'login', component: Stub, meta: { public: true } },
      { path: '/admin', name: 'admin', component: Stub, meta: { adminOnly: true } },
    ],
  })
  router.beforeEach(authGuard)
  return router
}

describe('authGuard', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
  })

  it('redirects unauthenticated users to /login preserving the target', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: 'not authenticated' }, 401))
    const router = makeRouter()

    await router.push('/?page=2')

    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/?page=2')
  })

  it('lets authenticated users through', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ id: 1, username: 'anna', display_name: 'Anna', preferences: { dashboard_fields: [] } }))
    const router = makeRouter()

    await router.push('/')

    expect(router.currentRoute.value.name).toBe('documents')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('sends signed-in users away from the login page', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ id: 1, username: 'anna', display_name: 'Anna', preferences: { dashboard_fields: [] } }))
    const router = makeRouter()

    await router.push('/login')

    expect(router.currentRoute.value.name).toBe('documents')
  })

  it('allows unauthenticated access to the login page', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: 'not authenticated' }, 401))
    const router = makeRouter()

    await router.push('/login')

    expect(router.currentRoute.value.name).toBe('login')
  })

  it('redirects non-admins away from an admin-only route', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ id: 1, username: 'anna', display_name: 'Anna', is_admin: false, preferences: { dashboard_fields: [] } }),
    )
    const router = makeRouter()

    await router.push('/admin')

    expect(router.currentRoute.value.name).toBe('documents')
  })

  it('lets admins reach an admin-only route', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ id: 2, username: 'root', display_name: 'Root', is_admin: true, preferences: { dashboard_fields: [] } }),
    )
    const router = makeRouter()

    await router.push('/admin')

    expect(router.currentRoute.value.name).toBe('admin')
  })

  describe('the OAuth consent route', () => {
    function makeAppRouter(): Router {
      // The app's real route table, so this pins the consent route's own meta.
      const router = createRouter({ history: createMemoryHistory(), routes })
      router.beforeEach(authGuard)
      return router
    }

    it('requires login and keeps the request id through the login redirect', async () => {
      fetchMock.mockResolvedValue(jsonResponse({ detail: 'not authenticated' }, 401))
      const router = makeAppRouter()

      await router.push('/oauth/consent?request=req_Ab-9')

      expect(router.currentRoute.value.name).toBe('login')
      const redirect = router.currentRoute.value.query.redirect
      expect(redirect).toBe('/oauth/consent?request=req_Ab-9')
      // LoginView pushes the redirect string after signing in; it must land
      // back on the consent page with the same request id.
      const back = router.resolve(redirect as string)
      expect(back.name).toBe('oauth-consent')
      expect(back.query.request).toBe('req_Ab-9')
    })

    it('lets a signed-in user through with the query intact', async () => {
      fetchMock.mockResolvedValue(
        jsonResponse({ id: 1, username: 'anna', display_name: 'Anna', is_admin: false, preferences: { dashboard_fields: [] } }),
      )
      const router = makeAppRouter()

      await router.push('/oauth/consent?request=req_Ab-9')

      expect(router.currentRoute.value.name).toBe('oauth-consent')
      expect(router.currentRoute.value.query.request).toBe('req_Ab-9')
    })
  })
})
