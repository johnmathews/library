import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { ApiError, apiFetch } from '@/api/client'
import {
  DEFAULT_BACKGROUND_TONE,
  DEFAULT_TILE_PREVIEW,
  type BackgroundTone,
  type DashboardField,
  type NotificationPreferences,
  type TilePreview,
  type UserPreferences,
} from '@/api/settings'

/** Empty notification preferences for users/payloads without the block. */
const DEFAULT_NOTIFICATION_PREFERENCES: NotificationPreferences = {
  enabled: false,
  pushover_app_token_set: false,
  pushover_user_key_set: false,
  pushover_device: null,
  events: [],
  email_forward_addresses: [],
}

export interface User {
  id: number
  username: string
  display_name: string
  is_admin: boolean
  preferences: UserPreferences
}

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const isAuthenticated = computed(() => user.value !== null)

  // Whether the signed-in user holds the admin role, gating the /admin area and
  // its sidebar link. Defaults to false when the user is absent (login page).
  const isAdmin = computed(() => user.value?.is_admin ?? false)

  const dashboardFields = computed<DashboardField[]>(
    () => user.value?.preferences?.dashboard_fields ?? [],
  )

  // The page-canvas tone, defaulting when the user is absent (e.g. login page)
  // or a payload predates the preference. Drives <html data-canvas> via App.vue.
  const backgroundTone = computed<BackgroundTone>(
    () => user.value?.preferences?.background_tone ?? DEFAULT_BACKGROUND_TONE,
  )

  // How dashboard tiles render the first-page thumbnail. Defaults when the
  // user is absent or a payload predates the preference.
  const tilePreview = computed<TilePreview>(
    () => user.value?.preferences?.tile_preview ?? DEFAULT_TILE_PREVIEW,
  )

  // The Pushover/forwarding notification settings, defaulting to an empty,
  // disabled block when the user is absent or a payload predates the feature.
  const notificationSettings = computed<NotificationPreferences>(
    () => user.value?.preferences?.notifications ?? { ...DEFAULT_NOTIFICATION_PREFERENCES },
  )

  function applyPreferences(preferences: UserPreferences): void {
    if (user.value) user.value.preferences = preferences
  }

  let mePromise: Promise<User | null> | null = null

  /**
   * Resolve the current user, calling GET /api/auth/me at most once
   * (subsequent calls reuse the cached result until login/logout).
   */
  function ensureLoaded(): Promise<User | null> {
    mePromise ??= apiFetch<User>('/api/auth/me')
      .then((me) => {
        user.value = me
        return me
      })
      .catch((error: unknown) => {
        if (error instanceof ApiError && error.status === 401) {
          user.value = null
          return null
        }
        mePromise = null // network or server error: retry next navigation
        throw error
      })
    return mePromise
  }

  async function login(username: string, password: string): Promise<User> {
    const me = await apiFetch<User>('/api/auth/login', {
      method: 'POST',
      body: { username, password },
    })
    user.value = me
    mePromise = Promise.resolve(me)
    return me
  }

  async function logout(): Promise<void> {
    await apiFetch<void>('/api/auth/logout', { method: 'POST' })
    user.value = null
    mePromise = Promise.resolve(null)
  }

  return {
    user,
    isAuthenticated,
    isAdmin,
    dashboardFields,
    backgroundTone,
    tilePreview,
    notificationSettings,
    applyPreferences,
    ensureLoaded,
    login,
    logout,
  }
})
