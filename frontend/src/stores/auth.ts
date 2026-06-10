import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { ApiError, apiFetch } from '@/api/client'

export interface User {
  id: number
  username: string
  display_name: string
}

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const isAuthenticated = computed(() => user.value !== null)

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

  return { user, isAuthenticated, ensureLoaded, login, logout }
})
