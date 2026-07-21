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
      phone_columns: 2,
      hide_summary_mobile: false,
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
      phone_columns: 2,
      hide_summary_mobile: false,
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

  it('saves the chosen dock position optimistically', async () => {
    const auth = useAuthStore()
    auth.user = {
      id: 1,
      username: 'a',
      display_name: 'A',
      is_admin: false,
      preferences: {
        dashboard_fields: ['kind'],
        background_tone: 'neutral',
        tile_preview: 'full_width',
        dock_position: 'top-right',
      },
    }
    stubFetch({
      dashboard_fields: ['kind'],
      background_tone: 'neutral',
      tile_preview: 'full_width',
      dock_position: 'bottom-left',
    })

    const wrapper = mount(SettingsView, { global: { stubs: { RouterLink: true } } })
    await wrapper.find('[data-testid="tab-appearance-btn"]').trigger('click')
    expect(wrapper.find('[data-testid="dock-position-top-right"]').attributes('aria-checked')).toBe('true')

    await wrapper.find('[data-testid="dock-position-bottom-left"]').trigger('click')
    // Optimistic: the store updates before the round-trip resolves.
    expect(auth.dockPosition).toBe('bottom-left')
    await flushPromises()

    const [url, init] = fetchMock.mock.calls.at(-1)!
    expect(String(url)).toBe('/api/settings/appearance')
    expect(init.method).toBe('PUT')
    expect(JSON.parse(init.body)).toEqual({
      background_tone: 'neutral',
      tile_preview: 'full_width',
      dock_position: 'bottom-left',
      phone_columns: 2,
      hide_summary_mobile: false,
    })
    expect(wrapper.find('[data-testid="dock-position-bottom-left"]').attributes('aria-checked')).toBe('true')
    expect(auth.dockPosition).toBe('bottom-left')
  })

  it('reverts the dock position and shows an error when the appearance save fails', async () => {
    const auth = useAuthStore()
    auth.user = {
      id: 1,
      username: 'a',
      display_name: 'A',
      is_admin: false,
      preferences: {
        dashboard_fields: ['kind'],
        background_tone: 'neutral',
        tile_preview: 'full_width',
        dock_position: 'top-right',
      },
    }
    stubFetch({ detail: 'boom' }, 500)

    const wrapper = mount(SettingsView, { global: { stubs: { RouterLink: true } } })
    await wrapper.find('[data-testid="tab-appearance-btn"]').trigger('click')
    await wrapper.find('[data-testid="dock-position-bottom-right"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="appearance-error"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="dock-position-top-right"]').attributes('aria-checked')).toBe('true')
    expect(auth.dockPosition).toBe('top-right')
  })

  it('persists a phone-columns choice via the appearance endpoint', async () => {
    const auth = useAuthStore()
    auth.user = {
      id: 1,
      username: 'a',
      display_name: 'A',
      is_admin: false,
      preferences: {
        dashboard_fields: ['kind'],
        background_tone: 'neutral',
        tile_preview: 'full_width',
        dock_position: 'top-right',
        phone_columns: 2,
      },
    }
    stubFetch({
      dashboard_fields: ['kind'],
      background_tone: 'neutral',
      tile_preview: 'full_width',
      dock_position: 'top-right',
      phone_columns: 3,
    })

    const wrapper = mount(SettingsView, { global: { stubs: { RouterLink: true } } })
    await wrapper.find('[data-testid="tab-appearance-btn"]').trigger('click')
    expect(wrapper.find('[data-testid="phone-columns-2"]').attributes('aria-checked')).toBe('true')

    await wrapper.find('[data-testid="phone-columns-3"]').trigger('click')
    // Optimistic: the store updates before the round-trip resolves.
    expect(auth.phoneColumns).toBe(3)
    await flushPromises()

    const [url, init] = fetchMock.mock.calls.at(-1)!
    expect(String(url)).toBe('/api/settings/appearance')
    expect(init.method).toBe('PUT')
    expect(JSON.parse(init.body)).toEqual({
      background_tone: 'neutral',
      tile_preview: 'full_width',
      dock_position: 'top-right',
      phone_columns: 3,
      hide_summary_mobile: false,
    })
    expect(wrapper.find('[data-testid="phone-columns-3"]').attributes('aria-checked')).toBe('true')
    expect(auth.phoneColumns).toBe(3)
  })

  it('reverts the phone-columns choice and shows an error when the appearance save fails', async () => {
    const auth = useAuthStore()
    auth.user = {
      id: 1,
      username: 'a',
      display_name: 'A',
      is_admin: false,
      preferences: {
        dashboard_fields: ['kind'],
        background_tone: 'neutral',
        tile_preview: 'full_width',
        dock_position: 'top-right',
        phone_columns: 2,
      },
    }
    stubFetch({ detail: 'boom' }, 500)

    const wrapper = mount(SettingsView, { global: { stubs: { RouterLink: true } } })
    await wrapper.find('[data-testid="tab-appearance-btn"]').trigger('click')
    await wrapper.find('[data-testid="phone-columns-3"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="appearance-error"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="phone-columns-2"]').attributes('aria-checked')).toBe('true')
    expect(auth.phoneColumns).toBe(2)
  })

  it('persists the hide-tile-description-on-mobile toggle via the appearance endpoint', async () => {
    const auth = useAuthStore()
    auth.user = {
      id: 1,
      username: 'a',
      display_name: 'A',
      is_admin: false,
      preferences: {
        dashboard_fields: ['kind'],
        background_tone: 'neutral',
        tile_preview: 'full_width',
        dock_position: 'top-right',
        phone_columns: 2,
        hide_summary_mobile: false,
      },
    }
    stubFetch({
      dashboard_fields: ['kind'],
      background_tone: 'neutral',
      tile_preview: 'full_width',
      dock_position: 'top-right',
      phone_columns: 2,
      hide_summary_mobile: true,
    })

    const wrapper = mount(SettingsView, { global: { stubs: { RouterLink: true } } })
    await wrapper.find('[data-testid="tab-appearance-btn"]').trigger('click')
    const toggle = wrapper.find('[data-testid="hide-summary-mobile"]')
    expect((toggle.element as HTMLInputElement).checked).toBe(false)

    await toggle.setValue(true)
    // Optimistic: the store updates before the round-trip resolves.
    expect(auth.hideSummaryMobile).toBe(true)
    await flushPromises()

    const [url, init] = fetchMock.mock.calls.at(-1)!
    expect(String(url)).toBe('/api/settings/appearance')
    expect(init.method).toBe('PUT')
    expect(JSON.parse(init.body)).toEqual({
      background_tone: 'neutral',
      tile_preview: 'full_width',
      dock_position: 'top-right',
      phone_columns: 2,
      hide_summary_mobile: true,
    })
    expect(auth.hideSummaryMobile).toBe(true)
  })

  it('reverts the hide-summary-mobile toggle and shows an error when the appearance save fails', async () => {
    const auth = useAuthStore()
    auth.user = {
      id: 1,
      username: 'a',
      display_name: 'A',
      is_admin: false,
      preferences: {
        dashboard_fields: ['kind'],
        background_tone: 'neutral',
        tile_preview: 'full_width',
        dock_position: 'top-right',
        phone_columns: 2,
        hide_summary_mobile: false,
      },
    }
    stubFetch({ detail: 'boom' }, 500)

    const wrapper = mount(SettingsView, { global: { stubs: { RouterLink: true } } })
    await wrapper.find('[data-testid="tab-appearance-btn"]').trigger('click')
    await wrapper.find('[data-testid="hide-summary-mobile"]').setValue(true)
    await flushPromises()

    expect(wrapper.find('[data-testid="appearance-error"]').exists()).toBe(true)
    expect((wrapper.find('[data-testid="hide-summary-mobile"]').element as HTMLInputElement).checked).toBe(false)
    expect(auth.hideSummaryMobile).toBe(false)
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

  describe('Email triage tab', () => {
    const baseTriage = {
      email_in_configured: true,
      poll_minutes: 10,
      held_folder: 'Library/Held',
      processed_folder: 'Library/Processed',
      hold: { enabled: true, below_substance: true, unknown_senders: true },
      allowlist: { configured: false, count: 0 },
      noise_filter: {
        enabled: true,
        tiny_image_max_bytes: 4096,
        tiny_image_max_edge_px: 64,
        decoration_max_bytes: 65536,
        decoration_max_edge_px: 384,
      },
      label: {
        enabled: true,
        active: true,
        model: 'claude-haiku-4-5',
        daily_budget_usd: 2,
        body_snippet_chars: 1000,
        prompt_version: 'email-label-v2',
      },
      body_substance: { min_words: 40, min_chars: 240 },
      imap_timeout_seconds: 60,
    }

    /** Route the triage endpoints to `triage`/`skips`; kinds and everything else as usual. */
    function stubTriageFetch(triage: unknown, skips: unknown = { recent_skips: [] }): void {
      fetchMock.mockImplementation((input: unknown) => {
        if (String(input) === '/api/kinds') return Promise.resolve(jsonResponse(KINDS))
        if (String(input) === '/api/settings/email-triage/recent-skips')
          return Promise.resolve(jsonResponse(skips))
        if (String(input) === '/api/settings/email-triage')
          return Promise.resolve(jsonResponse(triage))
        return Promise.resolve(jsonResponse({ dashboard_fields: ['kind'] }))
      })
    }

    async function mountAndOpenTab(triage: unknown, skips?: unknown) {
      const auth = useAuthStore()
      auth.user = { id: 1, username: 'a', display_name: 'A', is_admin: false, preferences: { dashboard_fields: ['kind'] } }
      stubTriageFetch(triage, skips)
      const wrapper = mount(SettingsView, { global: { stubs: { RouterLink: true } } })
      await wrapper.find('[data-testid="tab-email-triage-btn"]').trigger('click')
      await flushPromises()
      return wrapper
    }

    it('loads the config lazily when the tab is first shown', async () => {
      const wrapper = await mountAndOpenTab(baseTriage)
      const triageCalls = fetchMock.mock.calls.filter(
        ([url]) => String(url) === '/api/settings/email-triage',
      )
      expect(triageCalls).toHaveLength(1)
      expect(wrapper.find('[data-testid="email-triage-config"]').exists()).toBe(true)
      // Re-opening the tab does not refetch.
      await wrapper.find('[data-testid="tab-dashboard-btn"]').trigger('click')
      await wrapper.find('[data-testid="tab-email-triage-btn"]').trigger('click')
      await flushPromises()
      expect(
        fetchMock.mock.calls.filter(([url]) => String(url) === '/api/settings/email-triage'),
      ).toHaveLength(1)
    })

    it('renders the pipeline values, badges, and the held-emails link', async () => {
      const wrapper = await mountAndOpenTab(baseTriage)

      expect(wrapper.find('[data-testid="email-triage-hold-master"]').text()).toBe('Hold pipeline ON')
      expect(wrapper.find('[data-testid="view-held-emails"]').attributes('to')).toBe('/held-emails')
      expect(wrapper.find('[data-testid="triage-poll-minutes"]').text()).toContain('10 min')
      expect(wrapper.find('[data-testid="triage-held-folder"]').text()).toBe('Library/Held')
      expect(wrapper.find('[data-testid="triage-processed-folder"]').text()).toBe('Library/Processed')
      expect(wrapper.find('[data-testid="triage-imap-timeout"]').text()).toContain('60 s')

      // Step 1: accept-all variant (no allowlist configured), hold badge ON.
      expect(wrapper.find('[data-testid="triage-allowlist-mode"]').text()).toContain('Accept all senders')
      expect(wrapper.find('[data-testid="triage-allowlist-hold-badge"]').text()).toBe('Unknown-sender hold ON')
      // Step 2: noise gate ON with its tiny-image and decoration thresholds.
      expect(wrapper.find('[data-testid="triage-noise-badge"]').text()).toBe('ON')
      expect(wrapper.find('[data-testid="triage-noise-thresholds"]').text()).toContain('4096 bytes')
      expect(wrapper.find('[data-testid="triage-noise-thresholds"]').text()).toContain('64 px')
      expect(wrapper.find('[data-testid="triage-decoration-thresholds"]').text()).toContain(
        '65536 bytes',
      )
      expect(wrapper.find('[data-testid="triage-decoration-thresholds"]').text()).toContain(
        '384 px',
      )
      expect(wrapper.find('[data-testid="triage-decoration-thresholds"]').text()).toContain(
        'at least two signals',
      )
      // Step 3: label pass active, with model / budget / prompt version + fail-open.
      expect(wrapper.find('[data-testid="triage-label-badge"]').text()).toBe('Active')
      expect(wrapper.find('[data-testid="triage-label-state"]').text()).toBe('Active')
      expect(wrapper.find('[data-testid="triage-label-model"]').text()).toBe('claude-haiku-4-5')
      expect(wrapper.find('[data-testid="triage-label-budget"]').text()).toBe('$2')
      expect(wrapper.find('[data-testid="triage-label-prompt-version"]').text()).toBe('email-label-v2')
      expect(wrapper.find('[data-testid="triage-label-failopen"]').text()).toContain('Fail-open')
      // Step 4: substance thresholds + hold badge.
      expect(wrapper.find('[data-testid="triage-substance-thresholds"]').text()).toContain('40 words')
      expect(wrapper.find('[data-testid="triage-substance-thresholds"]').text()).toContain('240 characters')
      expect(wrapper.find('[data-testid="triage-substance-badge"]').text()).toBe('Below-substance hold ON')
      // Step 5 + footnote.
      expect(wrapper.find('[data-testid="triage-step-nothing"]').text()).toContain('held for review')
      expect(wrapper.find('[data-testid="email-triage-footnote"]').text()).toContain('email-triage.md')
    })

    it('shows the allowlist count and OFF badges when switches are disabled', async () => {
      const wrapper = await mountAndOpenTab({
        ...baseTriage,
        hold: { enabled: false, below_substance: false, unknown_senders: false },
        allowlist: { configured: true, count: 3 },
        noise_filter: { ...baseTriage.noise_filter, enabled: false },
      })

      expect(wrapper.find('[data-testid="email-triage-hold-master"]').text()).toBe('Hold pipeline OFF')
      expect(wrapper.find('[data-testid="triage-allowlist-mode"]').text()).toContain('3 allowed senders')
      expect(wrapper.find('[data-testid="triage-allowlist-hold-badge"]').text()).toBe('Unknown-sender hold OFF')
      expect(wrapper.find('[data-testid="triage-noise-badge"]').text()).toBe('OFF')
      expect(wrapper.find('[data-testid="triage-substance-badge"]').text()).toBe('Below-substance hold OFF')
    })

    it('distinguishes label inactive-without-key from disabled-by-flag', async () => {
      const noKey = await mountAndOpenTab({
        ...baseTriage,
        label: { ...baseTriage.label, enabled: true, active: false },
      })
      expect(noKey.find('[data-testid="triage-label-badge"]').text()).toBe('Inactive')
      expect(noKey.find('[data-testid="triage-label-state"]').text()).toContain('no API key')

      const disabled = await mountAndOpenTab({
        ...baseTriage,
        label: { ...baseTriage.label, enabled: false, active: false },
      })
      expect(disabled.find('[data-testid="triage-label-state"]').text()).toContain('disabled by configuration')
    })

    it('shows the unconfigured empty state instead of the flow', async () => {
      const wrapper = await mountAndOpenTab({ ...baseTriage, email_in_configured: false })
      const empty = wrapper.find('[data-testid="email-triage-unconfigured"]')
      expect(empty.exists()).toBe(true)
      expect(empty.text()).toContain('Email-in is not configured on this server')
      expect(wrapper.find('[data-testid="email-triage-config"]').exists()).toBe(false)
      // No email-in, no skip audit either.
      expect(wrapper.find('[data-testid="triage-recent-skips"]').exists()).toBe(false)
    })

    it('renders the recent skips with filename, reason, detail, and date', async () => {
      const wrapper = await mountAndOpenTab(baseTriage, {
        recent_skips: [
          {
            id: 7,
            message_id: '<m1@example.com>',
            subject: 'Fwd: invoice 42',
            from_address: 'alice@example.com',
            created_at: '2026-07-14T10:00:00Z',
            decisions: [
              {
                kind: 'attachment',
                filename: 'image001.png',
                reason: 'decoration_image',
                detail: 'filename, size and shape signals fired',
              },
            ],
          },
          {
            id: 6,
            message_id: null,
            subject: null,
            from_address: null,
            created_at: '2026-07-13T09:00:00Z',
            decisions: [
              { kind: 'attachment', filename: null, reason: 'tiny_image', detail: null },
            ],
          },
        ],
      })

      const card = wrapper.find('[data-testid="triage-recent-skips"]')
      expect(card.exists()).toBe(true)
      expect(wrapper.find('[data-testid="triage-recent-skips-empty"]').exists()).toBe(false)
      const row = wrapper.find('[data-testid="triage-skip-row-7"]')
      expect(row.text()).toContain('Fwd: invoice 42')
      expect(row.text()).toContain('alice@example.com')
      expect(row.text()).toContain('image001.png')
      expect(row.text()).toContain('decoration_image')
      expect(row.text()).toContain('filename, size and shape signals fired')
      expect(row.text()).toContain('2026') // the date renders
      // Null-safe fallbacks for a subject-less body skip.
      const fallback = wrapper.find('[data-testid="triage-skip-row-6"]')
      expect(fallback.text()).toContain('(no subject)')
      expect(fallback.text()).toContain('unknown sender')
      expect(fallback.text()).toContain('(attachment)') // unnamed part falls back to its kind
    })

    it('shows the recent-skips empty state when none are recorded', async () => {
      const wrapper = await mountAndOpenTab(baseTriage) // default stub: no skips
      const empty = wrapper.find('[data-testid="triage-recent-skips-empty"]')
      expect(empty.exists()).toBe(true)
      expect(empty.text()).toContain('No skipped items have been recorded yet')
      expect(wrapper.find('[data-testid="triage-recent-skips-list"]').exists()).toBe(false)
    })

    it('keeps the config visible when the recent skips fail to load', async () => {
      const auth = useAuthStore()
      auth.user = { id: 1, username: 'a', display_name: 'A', is_admin: false, preferences: { dashboard_fields: ['kind'] } }
      fetchMock.mockImplementation((input: unknown) => {
        if (String(input) === '/api/kinds') return Promise.resolve(jsonResponse(KINDS))
        if (String(input) === '/api/settings/email-triage/recent-skips')
          return Promise.resolve(jsonResponse({ detail: 'boom' }, 500))
        if (String(input) === '/api/settings/email-triage')
          return Promise.resolve(jsonResponse(baseTriage))
        return Promise.resolve(jsonResponse({ dashboard_fields: ['kind'] }))
      })
      const wrapper = mount(SettingsView, { global: { stubs: { RouterLink: true } } })
      await wrapper.find('[data-testid="tab-email-triage-btn"]').trigger('click')
      await flushPromises()

      expect(wrapper.find('[data-testid="email-triage-config"]').exists()).toBe(true)
      expect(wrapper.find('[data-testid="triage-recent-skips-error"]').text()).toContain(
        'could not be loaded',
      )
    })

    it('shows an error when the config cannot be loaded', async () => {
      const auth = useAuthStore()
      auth.user = { id: 1, username: 'a', display_name: 'A', is_admin: false, preferences: { dashboard_fields: ['kind'] } }
      fetchMock.mockImplementation((input: unknown) => {
        if (String(input) === '/api/kinds') return Promise.resolve(jsonResponse(KINDS))
        if (String(input) === '/api/settings/email-triage')
          return Promise.resolve(jsonResponse({ detail: 'boom' }, 500))
        return Promise.resolve(jsonResponse({ dashboard_fields: ['kind'] }))
      })
      const wrapper = mount(SettingsView, { global: { stubs: { RouterLink: true } } })
      await wrapper.find('[data-testid="tab-email-triage-btn"]').trigger('click')
      await flushPromises()
      expect(wrapper.find('[data-testid="email-triage-error"]').exists()).toBe(true)
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
