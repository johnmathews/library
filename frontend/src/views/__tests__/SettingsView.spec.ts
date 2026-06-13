import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import SettingsView from '../SettingsView.vue'
import { useAuthStore } from '@/stores/auth'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

describe('SettingsView', () => {
  const fetchMock = vi.fn()
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
  })

  it('renders the page heading', () => {
    const auth = useAuthStore()
    auth.user = { id: 1, username: 'a', display_name: 'A', preferences: { dashboard_fields: ['kind'] } }
    fetchMock.mockResolvedValue(jsonResponse({ dashboard_fields: ['kind'] }))
    const wrapper = mount(SettingsView, { global: { stubs: { RouterLink: true } } })
    expect(wrapper.find('h1').text()).toBe('Settings')
  })

  it('saves the selected fields and shows a confirmation', async () => {
    const auth = useAuthStore()
    auth.user = { id: 1, username: 'a', display_name: 'A', preferences: { dashboard_fields: ['kind'] } }
    fetchMock.mockResolvedValue(jsonResponse({ dashboard_fields: ['kind', 'tags'] }))

    const wrapper = mount(SettingsView, { global: { stubs: { RouterLink: true } } })
    // Loads current prefs: 'kind' arrives pre-checked from the store.
    expect(
      (wrapper.find('input.form-checkbox[value="kind"]').element as HTMLInputElement).checked,
    ).toBe(true)
    await wrapper.find('input.form-checkbox[value="tags"]').setValue(true)
    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()

    const [url, init] = fetchMock.mock.calls.at(-1)!
    expect(String(url)).toBe('/api/settings')
    expect(init.method).toBe('PUT')
    expect(wrapper.find('[data-testid="settings-saved"]').exists()).toBe(true)
    expect(auth.dashboardFields).toEqual(['kind', 'tags'])
  })

  it('shows an error and leaves prefs unchanged on save failure', async () => {
    const auth = useAuthStore()
    auth.user = { id: 1, username: 'a', display_name: 'A', preferences: { dashboard_fields: ['kind'] } }
    fetchMock.mockResolvedValue(jsonResponse({ detail: 'boom' }, 500))

    const wrapper = mount(SettingsView, { global: { stubs: { RouterLink: true } } })
    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()

    expect(wrapper.find('[data-testid="settings-error"]').exists()).toBe(true)
    expect(auth.dashboardFields).toEqual(['kind'])
  })
})
