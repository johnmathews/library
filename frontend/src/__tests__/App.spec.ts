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
})

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const Stub = { template: '<div />' }

describe('App service navigation', () => {
  const fetchMock = vi.fn()
  let router: Router
  let pinia: Pinia
  let wrapper: VueWrapper | undefined

  beforeEach(async () => {
    resetTaxonomyOptionsForTests()
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
        { path: '/login', name: 'login', component: Stub },
      ],
    })
    await router.push('/')
    await router.isReady()
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = undefined
    vi.unstubAllGlobals()
  })

  async function mountApp(): Promise<VueWrapper> {
    wrapper = mount(App, { global: { plugins: [router, pinia] } })
    const auth = useAuthStore()
    auth.user = { id: 1, username: 'e2e', display_name: 'E2E', preferences: { dashboard_fields: [] } }
    await flushPromises()
    return wrapper
  }

  it('places the Search button between Documents and Upload', async () => {
    const w = await mountApp()

    const items = w.findAll('.govuk-service-navigation__item .govuk-service-navigation__link')
    expect(items.map((item) => item.text())).toEqual(['Documents', 'Search', 'Upload', 'Sign out'])

    const search = items[1]!
    expect(search.element.tagName).toBe('BUTTON')
    expect(search.attributes('type')).toBe('button')
    expect(search.attributes('aria-haspopup')).toBe('dialog')
    expect(search.classes()).toContain('app-nav-button')
  })

  it('clicking the nav Search button opens the search modal', async () => {
    const w = await mountApp()
    const dialog = w.find('dialog[data-testid="search-modal"]')
    expect(dialog.exists()).toBe(true)
    expect(dialog.attributes('open')).toBeUndefined()

    await w
      .findAll('.govuk-service-navigation__link')
      .find((item) => item.text() === 'Search')!
      .trigger('click')
    await flushPromises()

    expect(dialog.attributes('open')).toBeDefined()
  })

  it('renders no navigation or search modal when signed out', async () => {
    wrapper = mount(App, { global: { plugins: [router, pinia] } })
    await flushPromises()
    expect(wrapper.find('.govuk-service-navigation').exists()).toBe(false)
    expect(wrapper.find('dialog').exists()).toBe(false)
  })
})
