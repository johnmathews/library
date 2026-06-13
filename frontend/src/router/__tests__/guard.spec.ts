import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import { authGuard } from '../index'

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
})
