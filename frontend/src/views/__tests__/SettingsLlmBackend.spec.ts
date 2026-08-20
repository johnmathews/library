import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import SettingsView from '../SettingsView.vue'
import { useAuthStore } from '@/stores/auth'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const KINDS = [{ slug: 'invoice', name: 'Invoice', document_count: 3 }]

const BACKENDS = {
  surfaces: [
    {
      surface: 'ask',
      label: 'Ask',
      description: 'The question-answering tool loop.',
      backend: 'api',
      default: 'api',
      overridden: false,
    },
    {
      surface: 'series_insight',
      label: 'Series descriptions',
      description: 'Cached prose per document series.',
      backend: 'api',
      default: 'api',
      overridden: false,
    },
  ],
  credentials_status: 'healthy',
  credentials_detail: 'access token valid (5.0h), refresh token present',
  api_key_configured: true,
  editable: true,
}

describe('SettingsView — LLM backend tab', () => {
  const fetchMock = vi.fn()

  /** Route each endpoint the tab touches; everything else falls through. */
  function routeFetch(overrides: Record<string, () => Response> = {}): void {
    fetchMock.mockImplementation((input: unknown, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      const key = `${method} ${url}`
      if (overrides[key]) return Promise.resolve(overrides[key]())
      if (url === '/api/kinds') return Promise.resolve(jsonResponse(KINDS))
      if (url === '/api/settings/llm-backends') return Promise.resolve(jsonResponse(BACKENDS))
      return Promise.resolve(jsonResponse({ dashboard_fields: ['kind'] }))
    })
  }

  async function openTab(isAdmin = true) {
    const auth = useAuthStore()
    auth.user = {
      id: 1,
      username: 'a',
      display_name: 'A',
      is_admin: isAdmin,
      preferences: { dashboard_fields: ['kind'] },
    }
    const wrapper = mount(SettingsView, { global: { stubs: { RouterLink: true } } })
    await flushPromises()
    await wrapper.find('[data-testid="tab-llm-backend-btn"]').trigger('click')
    await flushPromises()
    return wrapper
  }

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('does not fetch the backends until the tab is opened', async () => {
    routeFetch()
    const auth = useAuthStore()
    auth.user = {
      id: 1,
      username: 'a',
      display_name: 'A',
      is_admin: true,
      preferences: { dashboard_fields: ['kind'] },
    }
    mount(SettingsView, { global: { stubs: { RouterLink: true } } })
    await flushPromises()

    // Instance config nobody looks at on most visits, and the payload runs a
    // credential check — so it must not ride along with every Settings visit.
    const urls = fetchMock.mock.calls.map((call) => String(call[0]))
    expect(urls).not.toContain('/api/settings/llm-backends')
  })

  it('renders a row per surface with its current backend', async () => {
    routeFetch()
    const wrapper = await openTab()

    const ask = wrapper.find('[data-testid="llm-surface-ask"]')
    expect(ask.exists()).toBe(true)
    expect(ask.text()).toContain('Ask')
    expect(
      (wrapper.find('[data-testid="llm-backend-select-ask"]').element as HTMLSelectElement).value,
    ).toBe('api')
    expect(wrapper.find('[data-testid="llm-surface-series_insight"]').exists()).toBe(true)
  })

  it('shows credential status and never renders a key', async () => {
    routeFetch()
    const wrapper = await openTab()

    expect(wrapper.find('[data-testid="llm-credentials-status"]').text()).toBe('healthy')
    expect(wrapper.find('[data-testid="llm-credentials-detail"]').text()).toContain('refresh token present')
    expect(wrapper.find('[data-testid="llm-api-key-status"]').text()).toContain('Configured')
    expect(wrapper.text()).not.toContain('sk-ant')
  })

  it('saves a backend change and reports it applied', async () => {
    const switched = {
      ...BACKENDS,
      surfaces: [
        { ...BACKENDS.surfaces[0], backend: 'subscription', overridden: true },
        BACKENDS.surfaces[1],
      ],
    }
    routeFetch({ 'PUT /api/settings/llm-backends/ask': () => jsonResponse(switched) })
    const wrapper = await openTab()

    const select = wrapper.find('[data-testid="llm-backend-select-ask"]')
    ;(select.element as HTMLSelectElement).value = 'subscription'
    await select.trigger('change')
    await flushPromises()

    expect(wrapper.find('[data-testid="llm-backend-saved"]').exists()).toBe(true)
    expect(
      (wrapper.find('[data-testid="llm-backend-select-ask"]').element as HTMLSelectElement).value,
    ).toBe('subscription')
    // The deployed default is still visible, so "overridden" is not a mystery.
    expect(wrapper.find('[data-testid="llm-overridden-ask"]').text()).toContain('api')
  })

  it('surfaces the server reason when a change is refused', async () => {
    // A 409 carries the one actionable detail — what to run on the host. A
    // generic "could not be saved" would throw that away.
    routeFetch({
      'PUT /api/settings/llm-backends/ask': () =>
        jsonResponse(
          { detail: 'the Claude subscription is not usable: no credentials — run `claude auth login` on the host' },
          409,
        ),
    })
    const wrapper = await openTab()

    const select = wrapper.find('[data-testid="llm-backend-select-ask"]')
    ;(select.element as HTMLSelectElement).value = 'subscription'
    await select.trigger('change')
    await flushPromises()

    expect(wrapper.find('[data-testid="llm-backend-error"]').text()).toContain('claude auth login')
    expect(wrapper.find('[data-testid="llm-backend-saved"]').exists()).toBe(false)
  })

  it('reverts the control to the stored value after a refusal', async () => {
    // Otherwise the select keeps showing the rejected choice and the page lies
    // about what the server is doing.
    routeFetch({
      'PUT /api/settings/llm-backends/ask': () => jsonResponse({ detail: 'nope' }, 409),
    })
    const wrapper = await openTab()

    const select = wrapper.find('[data-testid="llm-backend-select-ask"]')
    ;(select.element as HTMLSelectElement).value = 'subscription'
    await select.trigger('change')
    await flushPromises()

    expect(
      (wrapper.find('[data-testid="llm-backend-select-ask"]').element as HTMLSelectElement).value,
    ).toBe('api')
  })

  it('is read-only for a non-admin', async () => {
    routeFetch({
      'GET /api/settings/llm-backends': () => jsonResponse({ ...BACKENDS, editable: false }),
    })
    const wrapper = await openTab(false)

    expect(
      (wrapper.find('[data-testid="llm-backend-select-ask"]').element as HTMLSelectElement).disabled,
    ).toBe(true)
    expect(wrapper.find('[data-testid="llm-readonly-note"]').exists()).toBe(true)
    // No reset affordance a non-admin could not actually use.
    expect(wrapper.find('[data-testid="llm-reset-ask"]').exists()).toBe(false)
  })

  it('offers a reset only for an overridden surface', async () => {
    const overridden = {
      ...BACKENDS,
      surfaces: [
        { ...BACKENDS.surfaces[0], backend: 'subscription', overridden: true },
        BACKENDS.surfaces[1],
      ],
    }
    routeFetch({
      'GET /api/settings/llm-backends': () => jsonResponse(overridden),
      'DELETE /api/settings/llm-backends/ask': () => jsonResponse(BACKENDS),
    })
    const wrapper = await openTab()

    expect(wrapper.find('[data-testid="llm-reset-series_insight"]').exists()).toBe(false)
    await wrapper.find('[data-testid="llm-reset-ask"]').trigger('click')
    await flushPromises()

    expect(
      (wrapper.find('[data-testid="llm-backend-select-ask"]').element as HTMLSelectElement).value,
    ).toBe('api')
    expect(wrapper.find('[data-testid="llm-overridden-ask"]').exists()).toBe(false)
  })

  it('reports a load failure instead of rendering an empty tab', async () => {
    routeFetch({
      'GET /api/settings/llm-backends': () => jsonResponse({ detail: 'boom' }, 500),
    })
    const wrapper = await openTab()

    expect(wrapper.find('[data-testid="llm-backend-error"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="llm-surfaces"]').exists()).toBe(false)
  })
})
