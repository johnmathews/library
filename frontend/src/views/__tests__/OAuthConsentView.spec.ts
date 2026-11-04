import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import OAuthConsentView from '../OAuthConsentView.vue'
import { useAuthStore } from '@/stores/auth'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const Stub = { template: '<div />' }

const REQUEST = {
  client_id: 'client-1',
  client_name: 'Claude',
  redirect_uri: 'https://claude.ai/api/mcp/auth_callback',
  redirect_host: 'claude.ai',
  scopes: ['library'],
  expires_at: '2026-11-04T19:10:00Z',
  expired: false,
}

const NOT_FOUND_DETAIL =
  'unknown or already answered authorization request, or one started in another browser; ' +
  'start connecting again from the app that sent you here'

const APPROVED = 'https://claude.ai/api/mcp/auth_callback?code=abc&state=s&iss=x'
const DENIED = 'https://claude.ai/api/mcp/auth_callback?error=access_denied&state=s&iss=x'

describe('OAuthConsentView', () => {
  const fetchMock = vi.fn()
  let assign: ReturnType<typeof vi.fn>
  let router: Router
  let pinia: Pinia
  let wrapper: VueWrapper | undefined

  /** Route each call by method + path; unknown calls fail loudly. */
  function stubApi(handlers: Record<string, () => Promise<Response> | Response>): void {
    fetchMock.mockImplementation((input: unknown, init?: RequestInit) => {
      const key = `${init?.method ?? 'GET'} ${String(input)}`
      const handler = handlers[key]
      if (!handler) return Promise.reject(new Error(`unexpected fetch: ${key}`))
      return Promise.resolve(handler())
    })
  }

  function calls(key: string): number {
    return fetchMock.mock.calls.filter(
      ([input, init]) => `${(init as RequestInit | undefined)?.method ?? 'GET'} ${String(input)}` === key,
    ).length
  }

  async function mountAt(path = '/oauth/consent?request=req-1'): Promise<VueWrapper> {
    await router.push(path)
    wrapper = mount(OAuthConsentView, { global: { plugins: [pinia, router] }, attachTo: document.body })
    await flushPromises()
    return wrapper
  }

  const approveBtn = (w: VueWrapper) => w.find('[data-testid="consent-approve"]')
  const denyBtn = (w: VueWrapper) => w.find('[data-testid="consent-deny"]')

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
    assign = vi.fn()
    vi.spyOn(window, 'location', 'get').mockReturnValue({ ...window.location, assign } as Location)
    document.cookie = 'library_csrftoken=csrf-1'
    pinia = createPinia()
    setActivePinia(pinia)
    const auth = useAuthStore()
    auth.user = {
      id: 7,
      username: 'anna',
      display_name: 'Anna de Vries',
      is_admin: false,
      preferences: { dashboard_fields: [] },
    }
    router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', name: 'documents', component: Stub },
        { path: '/login', name: 'login', component: Stub, meta: { public: true } },
        { path: '/oauth/consent', name: 'oauth-consent', component: Stub },
      ],
    })
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = undefined
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
    document.cookie = 'library_csrftoken=; expires=Thu, 01 Jan 1970 00:00:00 GMT'
  })

  it('shows the app, where the answer goes, who connects, and what it may do', async () => {
    stubApi({ 'GET /api/oauth/requests/req-1': () => jsonResponse(REQUEST) })
    const w = await mountAt()

    expect(w.find('h1').text()).toBe('Connect Claude to the Library?')
    expect(w.find('[data-testid="consent-redirect-host"]').text()).toBe('claude.ai')
    const account = w.find('[data-testid="consent-account"]').text()
    expect(account).toContain('Anna de Vries')
    expect(account).toContain('anna')
    const access = w.find('[data-testid="consent-access"]').text()
    expect(access).toMatch(/search and read/i)
    expect(access).toMatch(/add new documents/i)
    // Edit tools arrive with W7 of the MCP plan; until then the page must not promise them.
    expect(access).not.toMatch(/change document details/i)
    expect(approveBtn(w).attributes('disabled')).toBeUndefined()
    expect(denyBtn(w).attributes('disabled')).toBeUndefined()
  })

  it('names the app by its redirect host when it registered no name', async () => {
    stubApi({
      'GET /api/oauth/requests/req-1': () => jsonResponse({ ...REQUEST, client_name: null }),
    })
    const w = await mountAt()
    expect(w.find('h1').text()).toBe('Connect claude.ai to the Library?')
  })

  it('approve calls the API with the CSRF header and sends the browser to redirect_to', async () => {
    stubApi({
      'GET /api/oauth/requests/req-1': () => jsonResponse(REQUEST),
      'POST /api/oauth/requests/req-1/approve': () => jsonResponse({ redirect_to: APPROVED }),
    })
    const w = await mountAt()

    await approveBtn(w).trigger('click')
    await flushPromises()

    const [, init] = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/approve'))!
    expect((init.headers as Headers).get('X-CSRF-Token')).toBe('csrf-1')
    expect(assign).toHaveBeenCalledExactlyOnceWith(APPROVED)
    // The page is navigating away: the buttons stay off rather than invite a second answer.
    expect(approveBtn(w).attributes('disabled')).toBeDefined()
    expect(denyBtn(w).attributes('disabled')).toBeDefined()
  })

  it('deny calls the API and sends the browser to the access_denied redirect', async () => {
    stubApi({
      'GET /api/oauth/requests/req-1': () => jsonResponse(REQUEST),
      'POST /api/oauth/requests/req-1/deny': () => jsonResponse({ redirect_to: DENIED }),
    })
    const w = await mountAt()

    await denyBtn(w).trigger('click')
    await flushPromises()

    expect(calls('POST /api/oauth/requests/req-1/deny')).toBe(1)
    expect(calls('POST /api/oauth/requests/req-1/approve')).toBe(0)
    expect(assign).toHaveBeenCalledExactlyOnceWith(DENIED)
  })

  it('disables both buttons while an answer is in flight and ignores a second click', async () => {
    let release!: (response: Response) => void
    stubApi({
      'GET /api/oauth/requests/req-1': () => jsonResponse(REQUEST),
      'POST /api/oauth/requests/req-1/approve': () =>
        new Promise<Response>((resolve) => {
          release = resolve
        }),
    })
    const w = await mountAt()

    await approveBtn(w).trigger('click')
    await approveBtn(w).trigger('click')
    await denyBtn(w).trigger('click')

    expect(approveBtn(w).attributes('disabled')).toBeDefined()
    expect(denyBtn(w).attributes('disabled')).toBeDefined()
    expect(calls('POST /api/oauth/requests/req-1/approve')).toBe(1)
    expect(calls('POST /api/oauth/requests/req-1/deny')).toBe(0)

    release(jsonResponse({ redirect_to: APPROVED }))
    await flushPromises()
    expect(assign).toHaveBeenCalledExactlyOnceWith(APPROVED)
  })

  it('a 404 (unknown, answered, or another browser) says to start again from the app', async () => {
    stubApi({
      'GET /api/oauth/requests/req-1': () => jsonResponse({ detail: NOT_FOUND_DETAIL }, 404),
    })
    const w = await mountAt()

    const state = w.find('[data-testid="consent-not-found"]')
    expect(state.exists()).toBe(true)
    expect(state.text()).toMatch(/start connecting again from the app/i)
    expect(state.text()).toContain('another browser')
    expect(approveBtn(w).exists()).toBe(false)
    expect(denyBtn(w).exists()).toBe(false)
  })

  it('a link without a request id shows the start-again state without calling the API', async () => {
    stubApi({})
    const w = await mountAt('/oauth/consent')
    expect(w.find('[data-testid="consent-not-found"]').exists()).toBe(true)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('an expired request explains itself and offers only to tell the app (deny)', async () => {
    stubApi({
      'GET /api/oauth/requests/req-1': () => jsonResponse({ ...REQUEST, expired: true }),
      'POST /api/oauth/requests/req-1/deny': () => jsonResponse({ redirect_to: DENIED }),
    })
    const w = await mountAt()

    const state = w.find('[data-testid="consent-expired"]')
    expect(state.exists()).toBe(true)
    expect(state.text()).toMatch(/expired/i)
    expect(approveBtn(w).exists()).toBe(false)

    await w.find('[data-testid="consent-expired-return"]').trigger('click')
    await flushPromises()
    expect(calls('POST /api/oauth/requests/req-1/deny')).toBe(1)
    expect(assign).toHaveBeenCalledExactlyOnceWith(DENIED)
  })

  it('a 410 on approve switches to the expired state', async () => {
    stubApi({
      'GET /api/oauth/requests/req-1': () => jsonResponse(REQUEST),
      'POST /api/oauth/requests/req-1/approve': () =>
        jsonResponse({ detail: 'this authorization request expired' }, 410),
    })
    const w = await mountAt()

    await approveBtn(w).trigger('click')
    await flushPromises()

    expect(w.find('[data-testid="consent-expired"]').exists()).toBe(true)
    expect(assign).not.toHaveBeenCalled()
  })

  it('a 404 on approve switches to the start-again state', async () => {
    stubApi({
      'GET /api/oauth/requests/req-1': () => jsonResponse(REQUEST),
      'POST /api/oauth/requests/req-1/approve': () =>
        jsonResponse({ detail: NOT_FOUND_DETAIL }, 404),
    })
    const w = await mountAt()

    await approveBtn(w).trigger('click')
    await flushPromises()

    expect(w.find('[data-testid="consent-not-found"]').exists()).toBe(true)
    expect(assign).not.toHaveBeenCalled()
  })

  it('any other failure on approve shows an error and lets the user try again', async () => {
    stubApi({
      'GET /api/oauth/requests/req-1': () => jsonResponse(REQUEST),
      'POST /api/oauth/requests/req-1/approve': () => jsonResponse({ detail: 'boom' }, 500),
    })
    const w = await mountAt()

    await approveBtn(w).trigger('click')
    await flushPromises()

    expect(w.find('[data-testid="consent-submit-error"]').exists()).toBe(true)
    expect(assign).not.toHaveBeenCalled()
    expect(approveBtn(w).attributes('disabled')).toBeUndefined()
    expect(denyBtn(w).attributes('disabled')).toBeUndefined()
  })

  it('a failed load shows an error state with a retry', async () => {
    let fail = true
    stubApi({
      'GET /api/oauth/requests/req-1': () =>
        fail ? jsonResponse({ detail: 'boom' }, 500) : jsonResponse(REQUEST),
    })
    const w = await mountAt()

    expect(w.find('[data-testid="consent-load-error"]').exists()).toBe(true)
    expect(approveBtn(w).exists()).toBe(false)

    fail = false
    await w.find('[data-testid="consent-retry"]').trigger('click')
    await flushPromises()
    expect(approveBtn(w).exists()).toBe(true)
  })

  it('"Not you?" signs out and returns to login with the consent URL as the redirect', async () => {
    stubApi({
      'GET /api/oauth/requests/req-1': () => jsonResponse(REQUEST),
      'POST /api/auth/logout': () => new Response(null, { status: 204 }),
    })
    const w = await mountAt()

    await w.find('[data-testid="consent-switch-account"]').trigger('click')
    await flushPromises()

    expect(calls('POST /api/auth/logout')).toBe(1)
    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/oauth/consent?request=req-1')
  })
})
