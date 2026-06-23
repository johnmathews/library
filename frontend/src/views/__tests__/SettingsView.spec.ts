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
    expect(JSON.parse(init.body)).toEqual({ background_tone: 'slate', tile_preview: 'full_width' })
    expect(wrapper.find('[data-testid="tone-slate"]').attributes('aria-checked')).toBe('true')
    expect(auth.backgroundTone).toBe('slate')
  })

  it('selecting a tile preview in the Appearance tab saves and applies it', async () => {
    const auth = useAuthStore()
    auth.user = {
      id: 1,
      username: 'a',
      display_name: 'A',
      preferences: { dashboard_fields: ['kind'], background_tone: 'neutral', tile_preview: 'full_width' },
    }
    fetchMock.mockResolvedValue(
      jsonResponse({ dashboard_fields: ['kind'], background_tone: 'neutral', tile_preview: 'whole_page' }),
    )

    const wrapper = mount(SettingsView, { global: { stubs: { RouterLink: true } } })
    await wrapper.find('[data-testid="tab-appearance-btn"]').trigger('click')
    expect(wrapper.find('[data-testid="tile-full_width"]').attributes('aria-checked')).toBe('true')

    await wrapper.find('[data-testid="tile-whole_page"]').trigger('click')
    await flushPromises()

    const [url, init] = fetchMock.mock.calls.at(-1)!
    expect(String(url)).toBe('/api/settings/appearance')
    expect(JSON.parse(init.body)).toEqual({ background_tone: 'neutral', tile_preview: 'whole_page' })
    expect(wrapper.find('[data-testid="tile-whole_page"]').attributes('aria-checked')).toBe('true')
    expect(auth.tilePreview).toBe('whole_page')
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

  describe('Notifications tab', () => {
    const emptyNotifications = {
      enabled: false,
      pushover_app_token_set: false,
      pushover_user_key_set: false,
      pushover_device: null,
      events: [],
      email_forward_addresses: [],
    }

    it('saves notification settings and shows the success banner', async () => {
      const auth = useAuthStore()
      auth.user = {
        id: 1,
        username: 'a',
        display_name: 'A',
        preferences: { dashboard_fields: ['kind'], notifications: { ...emptyNotifications } },
      }
      fetchMock.mockResolvedValue(
        jsonResponse({
          dashboard_fields: ['kind'],
          notifications: {
            ...emptyNotifications,
            enabled: true,
            pushover_app_token_set: true,
            pushover_user_key_set: true,
            events: ['document_success'],
            email_forward_addresses: ['me@example.com'],
          },
        }),
      )

      const wrapper = mount(SettingsView, { global: { stubs: { RouterLink: true } } })
      await wrapper.find('[data-testid="tab-notifications-btn"]').trigger('click')

      await wrapper.find('[data-testid="notifications-enabled"]').setValue(true)
      await wrapper.find('#pushover-app-token').setValue('token-123')
      await wrapper.find('#pushover-user-key').setValue('user-456')
      await wrapper.find('input.form-checkbox[value="document_success"]').setValue(true)
      await wrapper.find('#email-forward-addresses').setValue('Me@Example.com\n\n')
      await wrapper.find('#settings-card-notifications form').trigger('submit.prevent')
      await flushPromises()

      const [url, init] = fetchMock.mock.calls.at(-1)!
      expect(String(url)).toBe('/api/settings/notifications')
      expect(init.method).toBe('PUT')
      const body = JSON.parse(init.body)
      expect(body.enabled).toBe(true)
      expect(body.pushover_app_token).toBe('token-123')
      expect(body.pushover_user_key).toBe('user-456')
      expect(body.events).toEqual(['document_success'])
      // Empty lines are trimmed client-side; lowercasing is left to the server.
      expect(body.email_forward_addresses).toEqual(['Me@Example.com'])

      expect(wrapper.find('[data-testid="notifications-saved"]').exists()).toBe(true)
      expect(auth.notificationSettings.enabled).toBe(true)
    })

    it('surfaces the 422 detail message on save failure', async () => {
      const auth = useAuthStore()
      auth.user = {
        id: 1,
        username: 'a',
        display_name: 'A',
        preferences: { dashboard_fields: ['kind'], notifications: { ...emptyNotifications } },
      }
      fetchMock.mockResolvedValue(
        jsonResponse({ detail: 'Pushover rejected the credentials' }, 422),
      )

      const wrapper = mount(SettingsView, { global: { stubs: { RouterLink: true } } })
      await wrapper.find('[data-testid="tab-notifications-btn"]').trigger('click')
      await wrapper.find('[data-testid="notifications-enabled"]').setValue(true)
      await wrapper.find('#settings-card-notifications form').trigger('submit.prevent')
      await flushPromises()

      const errorEl = wrapper.find('[data-testid="notifications-error"]')
      expect(errorEl.exists()).toBe(true)
      expect(errorEl.text()).toContain('Pushover rejected the credentials')
    })

    it('shows stored credentials as configured without exposing the value', async () => {
      const auth = useAuthStore()
      auth.user = {
        id: 1,
        username: 'a',
        display_name: 'A',
        preferences: {
          dashboard_fields: ['kind'],
          notifications: {
            ...emptyNotifications,
            enabled: true,
            pushover_app_token_set: true,
            pushover_user_key_set: true,
          },
        },
      }
      fetchMock.mockResolvedValue(jsonResponse({ dashboard_fields: ['kind'] }))

      const wrapper = mount(SettingsView, { global: { stubs: { RouterLink: true } } })
      await wrapper.find('[data-testid="tab-notifications-btn"]').trigger('click')

      const tokenInput = wrapper.find('#pushover-app-token').element as HTMLInputElement
      const keyInput = wrapper.find('#pushover-user-key').element as HTMLInputElement
      // The secret is never returned, so the entry fields stay blank...
      expect(tokenInput.value).toBe('')
      expect(keyInput.value).toBe('')
      // ...but the hint tells the user a value is already stored.
      expect(wrapper.find('#pushover-app-token-hint').text()).toContain('leave blank to keep')
      expect(wrapper.find('#pushover-user-key-hint').text()).toContain('leave blank to keep')
    })
  })
})
