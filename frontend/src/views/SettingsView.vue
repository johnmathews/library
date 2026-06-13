<script setup lang="ts">
/**
 * Settings: choose which metadata fields show on the dashboard tiles.
 * GOV.UK "select all that apply" checkboxes + save; the saved set lives
 * per-user on the server (PUT /api/settings) and in the auth store.
 */
import { ref } from 'vue'
import GovCheckboxes from '@/components/govuk/GovCheckboxes.vue'
import GovButton from '@/components/govuk/GovButton.vue'
import GovErrorSummary from '@/components/govuk/GovErrorSummary.vue'
import GovNotificationBanner from '@/components/govuk/GovNotificationBanner.vue'
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
  <div class="govuk-grid-row">
    <div class="govuk-grid-column-two-thirds">
      <h1 class="govuk-heading-xl">Settings</h1>

      <GovErrorSummary
        v-if="errorMessage"
        :errors="[{ text: errorMessage }]"
        data-testid="settings-error"
      />

      <GovNotificationBanner v-if="saved" variant="success" data-testid="settings-saved">
        <p class="govuk-notification-banner__heading">Your settings have been saved.</p>
      </GovNotificationBanner>

      <form @submit.prevent="onSubmit">
        <GovCheckboxes
          id="dashboard-fields"
          legend="Dashboard tile fields"
          legend-size="l"
          hint="Select all that apply. The document title and thumbnail are always shown."
          :items="items"
          :error-message="errorMessage ?? undefined"
          v-model="selected"
          small
        />
        <GovButton type="submit" :disabled="saving">Save changes</GovButton>
      </form>
    </div>
  </div>
</template>
