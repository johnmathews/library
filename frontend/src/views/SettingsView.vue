<script setup lang="ts">
/**
 * Settings, organised into tabs:
 *   - Dashboard: which metadata fields show on the dashboard tiles
 *     (checkboxes + explicit save; PUT /api/settings).
 *   - Appearance: the page-canvas tone behind the tiles — swatches that apply
 *     live and auto-save per click (PUT /api/settings/appearance).
 * Both preferences live per-user on the server and in the auth store.
 */
import { ref } from 'vue'
import { AppButton, AppBanner, AppCheckboxes, AppErrorSummary } from '@/components/app'
import {
  BACKGROUND_TONES,
  DASHBOARD_FIELDS,
  DEFAULT_BACKGROUND_TONE,
  updateAppearance,
  updateSettings,
  type BackgroundTone,
  type DashboardField,
} from '@/api/settings'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()

type Tab = 'dashboard' | 'appearance'
const tab = ref<Tab>('dashboard')

// --- Dashboard fields --------------------------------------------------------

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

// --- Appearance (page-canvas tone) ------------------------------------------

const selectedTone = ref<BackgroundTone>(auth.backgroundTone)
const toneError = ref<string | null>(null)

async function selectTone(tone: BackgroundTone): Promise<void> {
  if (tone === selectedTone.value) return
  const previous = selectedTone.value
  selectedTone.value = tone
  toneError.value = null
  // Optimistic: update the store now so App.vue's watcher repaints the canvas
  // instantly, before the round-trip resolves.
  if (auth.user) auth.applyPreferences({ ...auth.user.preferences, background_tone: tone })
  try {
    const result = await updateAppearance(tone)
    auth.applyPreferences(result)
    selectedTone.value = result.background_tone ?? DEFAULT_BACKGROUND_TONE
  } catch {
    selectedTone.value = previous
    if (auth.user) auth.applyPreferences({ ...auth.user.preferences, background_tone: previous })
    toneError.value = 'Sorry, your appearance preference could not be saved. Try again.'
  }
}

const cardClass =
  'bg-white dark:bg-gray-800 shadow-xs rounded-xl border border-gray-200 dark:border-gray-700/60 p-6'
const tabClass = (active: boolean): string =>
  [
    'px-4 py-2 -mb-px text-sm font-medium border-b-2 transition cursor-pointer',
    active
      ? 'border-violet-500 text-violet-600'
      : 'border-transparent text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200',
  ].join(' ')
</script>

<template>
  <div class="max-w-2xl">
    <h1 class="text-2xl md:text-3xl text-gray-800 dark:text-gray-100 font-bold mb-4">Settings</h1>

    <div role="tablist" class="flex gap-1 border-b border-gray-200 dark:border-gray-700/60 mb-6">
      <button
        role="tab"
        type="button"
        :aria-selected="tab === 'dashboard'"
        :tabindex="tab === 'dashboard' ? 0 : -1"
        :class="tabClass(tab === 'dashboard')"
        data-testid="tab-dashboard-btn"
        @click="tab = 'dashboard'"
      >
        Dashboard
      </button>
      <button
        role="tab"
        type="button"
        :aria-selected="tab === 'appearance'"
        :tabindex="tab === 'appearance' ? 0 : -1"
        :class="tabClass(tab === 'appearance')"
        data-testid="tab-appearance-btn"
        @click="tab = 'appearance'"
      >
        Appearance
      </button>
    </div>

    <!-- Dashboard tab -->
    <section v-show="tab === 'dashboard'" role="tabpanel" data-testid="tab-dashboard">
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

      <div :class="cardClass">
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
    </section>

    <!-- Appearance tab -->
    <section v-show="tab === 'appearance'" role="tabpanel" data-testid="tab-appearance">
      <AppErrorSummary
        v-if="toneError"
        :errors="[{ text: toneError }]"
        data-testid="appearance-error"
      />

      <div :class="cardClass">
        <fieldset>
          <legend class="text-lg font-semibold text-gray-800 dark:text-gray-100">
            Page background
          </legend>
          <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">
            The canvas colour behind your document tiles. Applies in light mode and saves to your
            account automatically.
          </p>
          <div role="radiogroup" aria-label="Page background" class="grid grid-cols-2 sm:grid-cols-3 gap-3 mt-5">
            <button
              v-for="tone in BACKGROUND_TONES"
              :key="tone.value"
              type="button"
              role="radio"
              :aria-checked="selectedTone === tone.value"
              :data-testid="`tone-${tone.value}`"
              :class="[
                'flex items-center gap-3 rounded-lg border p-3 text-left transition cursor-pointer',
                selectedTone === tone.value
                  ? 'border-violet-500 ring-2 ring-violet-500/30'
                  : 'border-gray-200 dark:border-gray-700/60 hover:border-gray-300 dark:hover:border-gray-600',
              ]"
              @click="selectTone(tone.value)"
            >
              <span
                class="w-8 h-8 shrink-0 rounded-md border border-gray-200 dark:border-gray-700/60"
                :style="{ backgroundColor: tone.swatch }"
              ></span>
              <span class="text-sm font-medium text-gray-700 dark:text-gray-200">{{
                tone.text
              }}</span>
            </button>
          </div>
        </fieldset>
      </div>
    </section>
  </div>
</template>
