import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import SettingsView from '../SettingsView.vue'
import { useAuthStore } from '@/stores/auth'
import { NEUTRAL_KIND_COLOR } from '@/api/settings'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

// Kinds are loaded on mount for the "Document type colours" editor. Routing this
// URL separately keeps the mount fetch from consuming a test's single mutation
// response (a Response body can only be read once).
const KINDS = [
  { slug: 'invoice', name: 'Invoice', document_count: 3 },
  { slug: 'receipt', name: 'Receipt', document_count: 1 },
  { slug: 'other', name: 'Other', document_count: 5 },
]

describe('SettingsView', () => {
  const fetchMock = vi.fn()

  /** Route GET /api/kinds to a fresh array; every other call to a fresh `body`. */
  function stubFetch(body: unknown, status = 200): void {
    fetchMock.mockImplementation((input: unknown) => {
      if (String(input) === '/api/kinds') return Promise.resolve(jsonResponse(KINDS))
      return Promise.resolve(jsonResponse(body, status))
    })
  }
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
    auth.user = { id: 1, username: 'a', display_name: 'A', is_admin: false, preferences: { dashboard_fields: ['kind'] } }
    stubFetch({ dashboard_fields: ['kind'] })
    const wrapper = mount(SettingsView, { global: { stubs: { RouterLink: true } } })
    expect(wrapper.find('h1').text()).toBe('Settings')
  })

  it('uses the shared PageHeader for the title', () => {
    const auth = useAuthStore()
    auth.user = { id: 1, username: 'a', display_name: 'A', is_admin: false, preferences: { dashboard_fields: ['kind'] } }
    stubFetch({ dashboard_fields: ['kind'] })
    const wrapper = mount(SettingsView, { global: { stubs: { RouterLink: true } } })
    const header = wrapper.find('[data-testid="page-header"]')
    expect(header.exists()).toBe(true)
    expect(header.find('h1').text()).toBe('Settings')
  })

  it('does not cap the page width on the view root', () => {
    const auth = useAuthStore()
    auth.user = { id: 1, username: 'a', display_name: 'A', is_admin: false, preferences: { dashboard_fields: ['kind'] } }
    stubFetch({ dashboard_fields: ['kind'] })
    const wrapper = mount(SettingsView, { global: { stubs: { RouterLink: true } } })
    const root = wrapper.find('#settings-page')
    expect(root.exists()).toBe(true)
    const rootClasses = root.classes()
    expect(rootClasses.some((cls) => cls.startsWith('max-w-'))).toBe(false)
  })

  it('does not cap the settings cards width (shell owns width)', () => {
    const auth = useAuthStore()
    auth.user = { id: 1, username: 'a', display_name: 'A', is_admin: false, preferences: { dashboard_fields: ['kind'] } }
    stubFetch({ dashboard_fields: ['kind'] })
    const wrapper = mount(SettingsView, { global: { stubs: { RouterLink: true } } })
    for (const id of ['#settings-card-dashboard-fields', '#settings-card-notifications']) {
      const card = wrapper.find(id)
      expect(card.exists()).toBe(true)
      expect(card.classes().some((cls) => cls.startsWith('max-w-'))).toBe(false)
    }
  })

  it('switches between tabs', async () => {
    const auth = useAuthStore()
    auth.user = { id: 1, username: 'a', display_name: 'A', is_admin: false, preferences: { dashboard_fields: ['kind'] } }
    stubFetch({ dashboard_fields: ['kind'] })
    const wrapper = mount(SettingsView, { global: { stubs: { RouterLink: true } } })

    expect(wrapper.find('[data-testid="tab-dashboard-btn"]').attributes('aria-selected')).toBe('true')

    await wrapper.find('[data-testid="tab-appearance-btn"]').trigger('click')
    expect(wrapper.find('[data-testid="tab-appearance-btn"]').attributes('aria-selected')).toBe('true')
    expect(wrapper.find('[data-testid="tab-dashboard-btn"]').attributes('aria-selected')).toBe('false')

    await wrapper.find('[data-testid="tab-notifications-btn"]').trigger('click')
    expect(wrapper.find('[data-testid="tab-notifications-btn"]').attributes('aria-selected')).toBe('true')
  })

  it('saves the selected fields and shows a confirmation', async () => {
    const auth = useAuthStore()
    auth.user = { id: 1, username: 'a', display_name: 'A', is_admin: false, preferences: { dashboard_fields: ['kind'] } }
    stubFetch({ dashboard_fields: ['kind', 'tags'] })

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
    auth.user = { id: 1, username: 'a', display_name: 'A', is_admin: false, preferences: { dashboard_fields: ['kind'] } }
    stubFetch({ detail: 'boom' }, 500)

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
      is_admin: false,
      preferences: { dashboard_fields: ['kind'], background_tone: 'neutral' },
    }
    stubFetch({ dashboard_fields: ['kind'], background_tone: 'slate' })

    const wrapper = mount(SettingsView, { global: { stubs: { RouterLink: true } } })
    await wrapper.find('[data-testid="tab-appearance-btn"]').trigger('click')
    // Default tone is selected to start.
    expect(wrapper.find('[data-testid="tone-neutral"]').attributes('aria-checked')).toBe('true')

    await wrapper.find('[data-testid="tone-slate"]').trigger('click')
    await flushPromises()

    const [url, init] = fetchMock.mock.calls.at(-1)!
    expect(String(url)).toBe('/api/settings/appearance')
    expect(init.method).toBe('PUT')
    expect(JSON.parse(init.body)).toEqual({
      background_tone: 'slate',
      tile_preview: 'full_width',
      dock_position: 'top-right',
    })
    expect(wrapper.find('[data-testid="tone-slate"]').attributes('aria-checked')).toBe('true')
    expect(auth.backgroundTone).toBe('slate')
  })

  it('selecting a tile preview in the Appearance tab saves and applies it', async () => {
    const auth = useAuthStore()
    auth.user = {
      id: 1,
      username: 'a',
      display_name: 'A',
      is_admin: false,
      preferences: { dashboard_fields: ['kind'], background_tone: 'neutral', tile_preview: 'full_width' },
    }
    stubFetch({ dashboard_fields: ['kind'], background_tone: 'neutral', tile_preview: 'whole_page' })

    const wrapper = mount(SettingsView, { global: { stubs: { RouterLink: true } } })
    await wrapper.find('[data-testid="tab-appearance-btn"]').trigger('click')
    expect(wrapper.find('[data-testid="tile-full_width"]').attributes('aria-checked')).toBe('true')

    await wrapper.find('[data-testid="tile-whole_page"]').trigger('click')
    await flushPromises()

    const [url, init] = fetchMock.mock.calls.at(-1)!
    expect(String(url)).toBe('/api/settings/appearance')
    expect(JSON.parse(init.body)).toEqual({
      background_tone: 'neutral',
      tile_preview: 'whole_page',
      dock_position: 'top-right',
    })
    expect(wrapper.find('[data-testid="tile-whole_page"]').attributes('aria-checked')).toBe('true')
    expect(auth.tilePreview).toBe('whole_page')
  })

  it('reverts the tone and shows an error when the appearance save fails', async () => {
    const auth = useAuthStore()
    auth.user = {
      id: 1,
      username: 'a',
      display_name: 'A',
      is_admin: false,
      preferences: { dashboard_fields: ['kind'], background_tone: 'neutral' },
    }
    stubFetch({ detail: 'boom' }, 500)

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
        is_admin: false,
        preferences: { dashboard_fields: ['kind'], notifications: { ...emptyNotifications } },
      }
      stubFetch({
          dashboard_fields: ['kind'],
          notifications: {
            ...emptyNotifications,
            enabled: true,
            pushover_app_token_set: true,
            pushover_user_key_set: true,
            events: ['document_success'],
            email_forward_addresses: ['me@example.com'],
          },
        })

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
        is_admin: false,
        preferences: { dashboard_fields: ['kind'], notifications: { ...emptyNotifications } },
      }
      stubFetch({ detail: 'Pushover rejected the credentials' }, 422)

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
        is_admin: false,
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
      stubFetch({ dashboard_fields: ['kind'] })

      const wrapper = mount(SettingsView, { global: { stubs: { RouterLink: true } } })
      await wrapper.find('[data-testid="tab-notifications-btn"]').trigger('click')

      const tokenInput = wrapper.find('#pushover-app-token').element as HTMLInputElement
      const keyInput = wrapper.find('#pushover-user-key').element as HTMLInputElement
      // The secret is never returned, so the entry fields stay blank...
      expect(tokenInput.value).toBe('')
      expect(keyInput.value).toBe('')
      // ...but a masked placeholder shows a value is already stored (instead
      // of an empty box), and the hint explains how to replace/keep it.
      expect(tokenInput.placeholder).toContain('•')
      expect(keyInput.placeholder).toContain('•')
      expect(wrapper.find('#pushover-app-token-hint').text()).toContain('leave blank to keep')
      expect(wrapper.find('#pushover-user-key-hint').text()).toContain('leave blank to keep')
    })

    it('eye toggle reveals what the user types in the secret fields', async () => {
      const auth = useAuthStore()
      auth.user = {
        id: 1,
        username: 'a',
        display_name: 'A',
        is_admin: false,
        preferences: {
          dashboard_fields: ['kind'],
          notifications: { ...emptyNotifications, pushover_app_token_set: true },
        },
      }
      stubFetch({ dashboard_fields: ['kind'] })

      const wrapper = mount(SettingsView, { global: { stubs: { RouterLink: true } } })
      await wrapper.find('[data-testid="tab-notifications-btn"]').trigger('click')

      const tokenInput = wrapper.find('#pushover-app-token').element as HTMLInputElement
      // Starts hidden (password), toggles to visible (text) and back.
      expect(tokenInput.type).toBe('password')
      await wrapper.find('[data-testid="pushover-app-token-reveal"]').trigger('click')
      expect(tokenInput.type).toBe('text')
      await wrapper.find('[data-testid="pushover-app-token-reveal"]').trigger('click')
      expect(tokenInput.type).toBe('password')
    })
  })

  describe('Document type colours', () => {
    function seedUser(kindColors: Record<string, string> = {}): ReturnType<typeof useAuthStore> {
      const auth = useAuthStore()
      auth.user = {
        id: 1,
        username: 'a',
        display_name: 'A',
        is_admin: false,
        preferences: { dashboard_fields: ['kind'], kind_colors: kindColors },
      }
      return auth
    }

    it('lists kinds most-used first, seeded with default/neutral colours', async () => {
      seedUser()
      stubFetch({ dashboard_fields: ['kind'] })
      const wrapper = mount(SettingsView, { global: { stubs: { RouterLink: true } } })
      await flushPromises() // let the kinds load resolve

      const rows = wrapper.findAll('[data-testid^="kind-color-row-"]')
      expect(rows.map((r) => r.attributes('data-testid'))).toEqual([
        'kind-color-row-other', // 5 docs
        'kind-color-row-invoice', // 3 docs
        'kind-color-row-receipt', // 1 doc
      ])
      // invoice picker seeded to its built-in default; 'other' is neutral.
      const invoiceInput = wrapper.find('[data-testid="kind-color-input-invoice"]')
        .element as HTMLInputElement
      const otherInput = wrapper.find('[data-testid="kind-color-input-other"]')
        .element as HTMLInputElement
      expect(invoiceInput.value).toBe('#56b1f3')
      expect(otherInput.value).toBe(NEUTRAL_KIND_COLOR)
      // Nothing customised yet: the per-kind Default + Reset all are disabled.
      expect(
        wrapper.find('[data-testid="kind-color-reset-invoice"]').attributes('disabled'),
      ).toBeDefined()
      expect(
        wrapper.find('[data-testid="kind-colors-reset-all"]').attributes('disabled'),
      ).toBeDefined()
    })

    it('saves a picked suggested colour and enables the reset controls', async () => {
      const auth = seedUser()
      stubFetch({ dashboard_fields: ['kind'], kind_colors: { invoice: '#755ff8' } })
      const wrapper = mount(SettingsView, { global: { stubs: { RouterLink: true } } })
      await flushPromises()

      const invoiceRow = wrapper.find('[data-testid="kind-color-row-invoice"]')
      await invoiceRow.find('button[title="Violet"]').trigger('click')
      await flushPromises()

      const [url, init] = fetchMock.mock.calls.at(-1)!
      expect(String(url)).toBe('/api/settings/kind-colors')
      expect(init.method).toBe('PUT')
      expect(JSON.parse(init.body)).toEqual({ kind_colors: { invoice: '#755ff8' } })
      expect(auth.kindColors).toEqual({ invoice: '#755ff8' })
      expect(
        wrapper.find('[data-testid="kind-colors-reset-all"]').attributes('disabled'),
      ).toBeUndefined()
    })

    it('resets a single kind back to its default', async () => {
      const auth = seedUser({ invoice: '#755ff8' })
      stubFetch({ dashboard_fields: ['kind'], kind_colors: {} })
      const wrapper = mount(SettingsView, { global: { stubs: { RouterLink: true } } })
      await flushPromises()

      const resetBtn = wrapper.find('[data-testid="kind-color-reset-invoice"]')
      expect(resetBtn.attributes('disabled')).toBeUndefined() // customised → enabled
      await resetBtn.trigger('click')
      await flushPromises()

      const [url, init] = fetchMock.mock.calls.at(-1)!
      expect(String(url)).toBe('/api/settings/kind-colors')
      expect(JSON.parse(init.body)).toEqual({ kind_colors: {} })
      expect(auth.kindColors).toEqual({})
    })

    it('reset all clears every override', async () => {
      const auth = seedUser({ invoice: '#755ff8', receipt: '#34bd68' })
      stubFetch({ dashboard_fields: ['kind'], kind_colors: {} })
      const wrapper = mount(SettingsView, { global: { stubs: { RouterLink: true } } })
      await flushPromises()

      await wrapper.find('[data-testid="kind-colors-reset-all"]').trigger('click')
      await flushPromises()

      const [url, init] = fetchMock.mock.calls.at(-1)!
      expect(String(url)).toBe('/api/settings/kind-colors')
      expect(JSON.parse(init.body)).toEqual({ kind_colors: {} })
      expect(auth.kindColors).toEqual({})
    })
  })
})
