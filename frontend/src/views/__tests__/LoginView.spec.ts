import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import LoginView from '../LoginView.vue'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const Stub = { template: '<div />' }

describe('LoginView', () => {
  const fetchMock = vi.fn()
  let router: Router
  let pinia: Pinia
  let wrapper: VueWrapper | undefined

  beforeEach(async () => {
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
    pinia = createPinia()
    setActivePinia(pinia)
    router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', name: 'documents', component: Stub },
        { path: '/login', name: 'login', component: Stub, meta: { public: true } },
      ],
    })
    await router.push('/login')
    document.body.classList.add('govuk-frontend-supported')
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = undefined
    vi.unstubAllGlobals()
    document.body.classList.remove('govuk-frontend-supported')
  })

  function mountView(): VueWrapper {
    wrapper = mount(LoginView, { global: { plugins: [pinia, router] }, attachTo: document.body })
    return wrapper
  }

  it('renders a sign-in form', () => {
    const w = mountView()
    expect(w.find('h1').text()).toBe('Library')
    expect(w.find('input#username').exists()).toBe(true)
    expect(w.find('input#password').attributes('type')).toBe('password')
    expect(w.find('button[type="submit"]').text()).toBe('Sign in')
    expect(w.find('[role="alert"]').exists()).toBe(false)
  })

  it('shows a focused error summary linking to fields when inputs are empty', async () => {
    const w = mountView()

    await w.find('form').trigger('submit')
    await flushPromises()

    const summary = w.find('[role="alert"]')
    expect(summary.exists()).toBe(true)
    expect(document.activeElement).toBe(summary.element)

    const links = summary.findAll('a')
    expect(links.map((l) => l.attributes('href'))).toEqual(['#username', '#password'])
    expect(w.find('#username-error').text()).toContain('Enter your username')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('shows the generic error summary after a 401 from the API', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: 'invalid credentials' }, 401))
    const w = mountView()

    await w.find('#username').setValue('anna')
    await w.find('#password').setValue('wrong')
    await w.find('form').trigger('submit')
    await flushPromises()

    const summary = w.find('[role="alert"]')
    expect(summary.text()).toContain('Enter a correct username and password')
    expect(summary.find('a').attributes('href')).toBe('#username')
    expect(document.activeElement).toBe(summary.element)
  })

  it('redirects to the original target after a successful login', async () => {
    await router.push('/login?redirect=/?page=3')
    fetchMock.mockResolvedValue(
      jsonResponse({ id: 1, username: 'anna', display_name: 'Anna', preferences: { dashboard_fields: [] } }),
    )
    const w = mountView()

    await w.find('#username').setValue('anna')
    await w.find('#password').setValue('hunter2')
    await w.find('form').trigger('submit')
    await flushPromises()

    expect(router.currentRoute.value.fullPath).toBe('/?page=3')
  })
})
