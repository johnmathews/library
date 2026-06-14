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

  it('selecting a background tone in the Appearance tab saves and applies it', async () => {
    const auth = useAuthStore()
    auth.user = {
      id: 1,
      username: 'a',
      display_name: 'A',
      preferences: { dashboard_fields: ['kind'], background_tone: 'neutral' },
    }
    fetchMock.mockResolvedValue(
      jsonResponse({ dashboard_fields: ['kind'], background_tone: 'slate' }),
    )

    const wrapper = mount(SettingsView, { global: { stubs: { RouterLink: true } } })
    await wrapper.find('[data-testid="tab-appearance-btn"]').trigger('click')
    // Default tone is selected to start.
    expect(wrapper.find('[data-testid="tone-neutral"]').attributes('aria-checked')).toBe('true')

    await wrapper.find('[data-testid="tone-slate"]').trigger('click')
    await flushPromises()

    const [url, init] = fetchMock.mock.calls.at(-1)!
    expect(String(url)).toBe('/api/settings/appearance')
    expect(init.method).toBe('PUT')
    expect(JSON.parse(init.body)).toEqual({ background_tone: 'slate' })
    expect(wrapper.find('[data-testid="tone-slate"]').attributes('aria-checked')).toBe('true')
    expect(auth.backgroundTone).toBe('slate')
  })

  it('reverts the tone and shows an error when the appearance save fails', async () => {
    const auth = useAuthStore()
    auth.user = {
      id: 1,
      username: 'a',
      display_name: 'A',
      preferences: { dashboard_fields: ['kind'], background_tone: 'neutral' },
    }
    fetchMock.mockResolvedValue(jsonResponse({ detail: 'boom' }, 500))

    const wrapper = mount(SettingsView, { global: { stubs: { RouterLink: true } } })
    await wrapper.find('[data-testid="tab-appearance-btn"]').trigger('click')
    await wrapper.find('[data-testid="tone-mist"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="appearance-error"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="tone-neutral"]').attributes('aria-checked')).toBe('true')
    expect(auth.backgroundTone).toBe('neutral')
  })
})
