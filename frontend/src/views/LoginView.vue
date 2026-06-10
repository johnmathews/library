<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import GovButton from '@/components/govuk/GovButton.vue'
import GovErrorSummary from '@/components/govuk/GovErrorSummary.vue'
import GovInput from '@/components/govuk/GovInput.vue'
import type { ErrorSummaryItem } from '@/components/govuk'
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
  <div class="govuk-grid-row">
    <div class="govuk-grid-column-two-thirds">
      <GovErrorSummary v-if="summaryErrors.length" :errors="summaryErrors" />

      <h1 class="govuk-heading-xl">Sign in</h1>

      <form novalidate @submit.prevent="onSubmit">
        <GovInput
          id="username"
          v-model="username"
          label="Username"
          autocomplete="username"
          :spellcheck="false"
          width-class="govuk-!-width-two-thirds"
          :error-message="fieldErrors.username"
        />
        <GovInput
          id="password"
          v-model="password"
          label="Password"
          type="password"
          autocomplete="current-password"
          width-class="govuk-!-width-two-thirds"
          :error-message="fieldErrors.password"
        />
        <GovButton type="submit" :disabled="submitting">Sign in</GovButton>
      </form>
    </div>
  </div>
</template>
