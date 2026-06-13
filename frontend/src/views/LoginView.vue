<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { AppButton, AppErrorSummary, AppInput } from '@/components/app'
import type { ErrorSummaryItem } from '@/components/app'
import { ApiError } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const username = ref('')
const password = ref('')
const submitting = ref(false)

const fieldErrors = reactive<{ username?: string; password?: string }>({})
const summaryErrors = ref<ErrorSummaryItem[]>([])

async function onSubmit(): Promise<void> {
  if (submitting.value) return

  delete fieldErrors.username
  delete fieldErrors.password
  const errors: ErrorSummaryItem[] = []

  if (!username.value.trim()) {
    fieldErrors.username = 'Enter your username'
    errors.push({ text: 'Enter your username', href: '#username' })
  }
  if (!password.value) {
    fieldErrors.password = 'Enter your password'
    errors.push({ text: 'Enter your password', href: '#password' })
  }
  if (errors.length) {
    summaryErrors.value = errors
    return
  }

  submitting.value = true
  try {
    await auth.login(username.value.trim(), password.value)
    summaryErrors.value = []
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
    await router.push(redirect)
  } catch (error: unknown) {
    if (error instanceof ApiError && error.status === 401) {
      summaryErrors.value = [
        { text: 'Enter a correct username and password', href: '#username' },
      ]
    } else {
      summaryErrors.value = [
        { text: 'Sorry, there is a problem with the service. Try again later.' },
      ]
    }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="min-h-[100dvh] flex items-center justify-center bg-gray-100 dark:bg-gray-900 px-4">
    <div
      class="w-full max-w-md bg-white dark:bg-gray-800 shadow-xs rounded-xl border border-gray-200 dark:border-gray-700/60 p-8"
    >
      <h1 class="text-2xl font-bold text-gray-800 dark:text-gray-100 mb-6">Library</h1>

      <AppErrorSummary v-if="summaryErrors.length" :errors="summaryErrors" />

      <form class="space-y-4" novalidate @submit.prevent="onSubmit">
        <AppInput
          id="username"
          v-model="username"
          label="Username"
          autocomplete="username"
          :spellcheck="false"
          :error-message="fieldErrors.username"
        />
        <AppInput
          id="password"
          v-model="password"
          label="Password"
          type="password"
          autocomplete="current-password"
          :error-message="fieldErrors.password"
        />
        <AppButton type="submit" class="w-full" :disabled="submitting">Sign in</AppButton>
      </form>
    </div>
  </div>
</template>
