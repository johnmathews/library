import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import App from '@/App.vue'
import { resetTaxonomyOptionsForTests } from '@/composables/taxonomyOptions'
import { useAuthStore } from '@/stores/auth'

// jsdom 29 ships no showModal()/close() on HTMLDialogElement — stub the
// happy path (see SearchModal.spec.ts for the full note).
beforeAll(() => {
  if (typeof HTMLDialogElement.prototype.showModal !== 'function') {
    HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) {
      this.setAttribute('open', '')
    }
  }
  if (typeof HTMLDialogElement.prototype.close !== 'function') {
    HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) {
      this.removeAttribute('open')
      this.dispatchEvent(new Event('close'))
    }
  }
  // AppSidebar reads window.matchMedia for its expand-by-default heuristic;
  // jsdom does not provide it.
  if (!window.matchMedia) {
    window.matchMedia = (() => ({
      matches: false,
      media: '',
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
      dispatchEvent() {
        return false
      },
    })) as unknown as typeof window.matchMedia
  }
})

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const Stub = { template: '<div />' }

describe('App Mosaic shell', () => {
  const fetchMock = vi.fn()
  let router: Router
  let pinia: Pinia
  let wrapper: VueWrapper | undefined

  beforeEach(async () => {
    resetTaxonomyOptionsForTests()
    localStorage.clear()
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
    fetchMock.mockImplementation((input: unknown) => {
      const url = String(input)
      if (url === '/api/kinds' || url === '/api/senders' || url === '/api/tags') {
        return Promise.resolve(jsonResponse([]))
      }
      return Promise.resolve(jsonResponse({ detail: `unexpected ${url}` }, 500))
    })
    pinia = createPinia()
    setActivePinia(pinia)
    router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', name: 'documents', component: Stub },
        { path: '/upload', name: 'upload', component: Stub },
        { path: '/settings', name: 'settings', component: Stub },
        { path: '/login', name: 'login', component: Stub, meta: { public: true } },
      ],
    })
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = undefined
    vi.unstubAllGlobals()
  })

  it('renders no sidebar on a public route (login)', async () => {
    await router.push('/login')
    await router.isReady()
    wrapper = mount(App, { global: { plugins: [router, pinia] } })
    await flushPromises()
    expect(wrapper.find('#sidebar').exists()).toBe(false)
  })

  it('renders the Mosaic sidebar on a private route when authenticated', async () => {
    await router.push('/')
    await router.isReady()
    const auth = useAuthStore()
    auth.user = {
      id: 1,
      username: 'e2e',
      display_name: 'E2E',
      is_admin: false,
      preferences: { dashboard_fields: [] },
    }
    wrapper = mount(App, { global: { plugins: [router, pinia] } })
    await flushPromises()
    expect(wrapper.find('#sidebar').exists()).toBe(true)
  })
})
