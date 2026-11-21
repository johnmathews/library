<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { AppButton, AppErrorSummary, AppInput } from '@/components/app'
import AppLogo from '@/components/layout/AppLogo.vue'
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
  <div id="login-page" class="min-h-[100dvh] flex items-center justify-center bg-canvas px-4">
    <div id="login-card" class="card w-full max-w-md p-8">
      <div class="login-logo mb-6" data-testid="login-logo">
        <AppLogo />
      </div>
      <h1 class="text-2xl font-[650] text-ink mb-6">Sign in</h1>

      <AppErrorSummary v-if="summaryErrors.length" :errors="summaryErrors" />

      <form id="login-form" class="space-y-4" novalidate @submit.prevent="onSubmit">
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

<style scoped>
/* AppLogo hides its name at lg+ unless the desktop sidebar is expanded. The
   sign-in page has no sidebar, so the name always shows here. */
.login-logo :deep(.app-logo__name) {
  opacity: 1;
}
</style>
