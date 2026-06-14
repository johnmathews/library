import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { ApiError, apiFetch } from '@/api/client'
import {
  DEFAULT_BACKGROUND_TONE,
  DEFAULT_TILE_PREVIEW,
  type BackgroundTone,
  type DashboardField,
  type TilePreview,
  type UserPreferences,
} from '@/api/settings'

export interface User {
  id: number
  username: string
  display_name: string
  preferences: UserPreferences
}

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const isAuthenticated = computed(() => user.value !== null)

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
    dashboardFields,
    backgroundTone,
    tilePreview,
    applyPreferences,
    ensureLoaded,
    login,
    logout,
  }
})
