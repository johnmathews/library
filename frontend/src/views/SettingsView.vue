<script setup lang="ts">
/**
 * Settings: choose which metadata fields show on the dashboard tiles.
 * "Select all that apply" checkboxes + save; the saved set lives per-user on
 * the server (PUT /api/settings) and in the auth store.
 */
import { ref } from 'vue'
import { AppButton, AppBanner, AppCheckboxes, AppErrorSummary } from '@/components/app'
import { DASHBOARD_FIELDS, updateSettings, type DashboardField } from '@/api/settings'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()

const items = DASHBOARD_FIELDS.map((field) => ({ value: field.value, text: field.text }))

// Seed the checkbox model from the store's current preferences.
const selected = ref<string[]>([...auth.dashboardFields])

const saved = ref(false)
const errorMessage = ref<string | null>(null)
const saving = ref(false)

async function onSubmit(): Promise<void> {
  saving.value = true
  saved.value = false
  errorMessage.value = null
  try {
    const fields = selected.value as DashboardField[]
    const result = await updateSettings({ dashboard_fields: fields })
    auth.applyPreferences(result)
    // Reflect the server-cleaned set back into the form.
    selected.value = [...result.dashboard_fields]
    saved.value = true
  } catch {
    errorMessage.value = 'Sorry, your settings could not be saved. Try again.'
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="max-w-2xl">
    <h1 class="text-2xl md:text-3xl text-gray-800 dark:text-gray-100 font-bold mb-2">Settings</h1>

    <AppErrorSummary
      v-if="errorMessage"
      :errors="[{ text: errorMessage }]"
      data-testid="settings-error"
    />

    <div v-if="saved" class="mb-6">
      <AppBanner variant="success" data-testid="settings-saved">
        <p>Your settings have been saved.</p>
      </AppBanner>
    </div>

    <div
      class="bg-white dark:bg-gray-800 shadow-xs rounded-xl border border-gray-200 dark:border-gray-700/60 p-6"
    >
      <form @submit.prevent="onSubmit">
        <AppCheckboxes
          id="dashboard-fields"
          legend="Dashboard tile fields"
          legend-size="l"
          hint="Select all that apply. The document title and thumbnail are always shown."
          :items="items"
          :error-message="errorMessage ?? undefined"
          v-model="selected"
          small
        />
        <div class="mt-6">
          <AppButton type="submit" :disabled="saving">Save changes</AppButton>
        </div>
      </form>
    </div>
  </div>
</template>
