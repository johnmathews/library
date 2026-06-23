<script setup lang="ts">
/**
 * Settings, organised into tabs:
 *   - Dashboard: which metadata fields show on the dashboard tiles
 *     (checkboxes + explicit save; PUT /api/settings).
 *   - Appearance: the page-canvas tone behind the tiles — swatches that apply
 *     live and auto-save per click (PUT /api/settings/appearance).
 *   - Notifications: Pushover push notifications + the email addresses a user
 *     forwards documents from (explicit save; PUT /api/settings/notifications).
 * All preferences live per-user on the server and in the auth store.
 */
import { ref } from 'vue'
import { AppButton, AppBanner, AppCheckboxes, AppErrorSummary, AppInput, AppTextarea } from '@/components/app'
import { ApiError } from '@/api/client'
import {
  BACKGROUND_TONES,
  DASHBOARD_FIELDS,
  DEFAULT_BACKGROUND_TONE,
  DEFAULT_TILE_PREVIEW,
  NOTIFICATION_EVENTS,
  TILE_PREVIEWS,
  updateAppearance,
  updateNotifications,
  updateSettings,
  type BackgroundTone,
  type DashboardField,
  type TilePreview,
} from '@/api/settings'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()

type Tab = 'dashboard' | 'appearance' | 'notifications'
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
    const result = await updateAppearance(tone, selectedTilePreview.value)
    auth.applyPreferences(result)
    selectedTone.value = result.background_tone ?? DEFAULT_BACKGROUND_TONE
  } catch {
    selectedTone.value = previous
    if (auth.user) auth.applyPreferences({ ...auth.user.preferences, background_tone: previous })
    toneError.value = 'Sorry, your appearance preference could not be saved. Try again.'
  }
}

// --- Appearance (document tile preview) -------------------------------------

const selectedTilePreview = ref<TilePreview>(auth.tilePreview)

async function selectTilePreview(mode: TilePreview): Promise<void> {
  if (mode === selectedTilePreview.value) return
  const previous = selectedTilePreview.value
  selectedTilePreview.value = mode
  toneError.value = null
  // Optimistic: update the store now so the dashboard reflects it immediately.
  if (auth.user) auth.applyPreferences({ ...auth.user.preferences, tile_preview: mode })
  try {
    const result = await updateAppearance(selectedTone.value, mode)
    auth.applyPreferences(result)
    selectedTilePreview.value = result.tile_preview ?? DEFAULT_TILE_PREVIEW
  } catch {
    selectedTilePreview.value = previous
    if (auth.user) auth.applyPreferences({ ...auth.user.preferences, tile_preview: previous })
    toneError.value = 'Sorry, your appearance preference could not be saved. Try again.'
  }
}

// --- Notifications (Pushover + email forwarding) ----------------------------

const notificationEventItems = NOTIFICATION_EVENTS.map((event) => ({
  value: event.value,
  text: event.label,
}))

// Seed the form from the store's current notification preferences. Secrets are
// never returned, so the token/key inputs start blank: a blank value on submit
// keeps the stored secret unchanged when the corresponding *_set flag is true.
const notifEnabled = ref<boolean>(auth.notificationSettings.enabled)
const notifAppToken = ref<string>('')
const notifUserKey = ref<string>('')
const notifDevice = ref<string>(auth.notificationSettings.pushover_device ?? '')
const notifEvents = ref<string[]>([...auth.notificationSettings.events])
const notifAddresses = ref<string>(auth.notificationSettings.email_forward_addresses.join('\n'))
const notifAppTokenSet = ref<boolean>(auth.notificationSettings.pushover_app_token_set)
const notifUserKeySet = ref<boolean>(auth.notificationSettings.pushover_user_key_set)

const notifSaved = ref(false)
const notifError = ref<string | null>(null)
const notifSaving = ref(false)

/** Split a textarea of comma/newline-separated addresses into a trimmed list. */
function parseAddresses(raw: string): string[] {
  return raw
    .split(/[\n,]/)
    .map((address) => address.trim())
    .filter((address) => address.length > 0)
}

/** Re-seed the form fields from a fresh notification read model. */
function seedNotifications(): void {
  const prefs = auth.notificationSettings
  notifEnabled.value = prefs.enabled
  notifDevice.value = prefs.pushover_device ?? ''
  notifEvents.value = [...prefs.events]
  notifAddresses.value = prefs.email_forward_addresses.join('\n')
  notifAppTokenSet.value = prefs.pushover_app_token_set
  notifUserKeySet.value = prefs.pushover_user_key_set
  // Secrets are never returned, so clear the entry fields after a save.
  notifAppToken.value = ''
  notifUserKey.value = ''
}

async function onNotificationsSubmit(): Promise<void> {
  notifSaving.value = true
  notifSaved.value = false
  notifError.value = null
  try {
    const result = await updateNotifications({
      enabled: notifEnabled.value,
      // Send the secret only when the user typed one; a blank value keeps the
      // stored secret unchanged on the server.
      pushover_app_token: notifAppToken.value.trim() || undefined,
      pushover_user_key: notifUserKey.value.trim() || undefined,
      pushover_device: notifDevice.value.trim() || null,
      events: notifEvents.value,
      email_forward_addresses: parseAddresses(notifAddresses.value),
    })
    auth.applyPreferences(result)
    seedNotifications()
    notifSaved.value = true
  } catch (error) {
    notifError.value =
      error instanceof ApiError
        ? error.detail
        : 'Sorry, your notification settings could not be saved. Try again.'
  } finally {
    notifSaving.value = false
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
  <div id="settings-page" class="max-w-2xl">
    <h1 class="text-2xl md:text-3xl text-gray-800 dark:text-gray-100 font-bold mb-4">Settings</h1>

    <div id="settings-tablist" role="tablist" class="flex gap-1 border-b border-gray-200 dark:border-gray-700/60 mb-6">
      <button
        id="settings-tab-dashboard"
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
        id="settings-tab-appearance"
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
      <button
        id="settings-tab-notifications"
        role="tab"
        type="button"
        :aria-selected="tab === 'notifications'"
        :tabindex="tab === 'notifications' ? 0 : -1"
        :class="tabClass(tab === 'notifications')"
        data-testid="tab-notifications-btn"
        @click="tab = 'notifications'"
      >
        Notifications
      </button>
    </div>

    <!-- Dashboard tab -->
    <section id="settings-panel-dashboard" v-show="tab === 'dashboard'" role="tabpanel" data-testid="tab-dashboard">
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

      <div id="settings-card-dashboard-fields" :class="cardClass">
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
    <section id="settings-panel-appearance" v-show="tab === 'appearance'" role="tabpanel" data-testid="tab-appearance">
      <AppErrorSummary
        v-if="toneError"
        :errors="[{ text: toneError }]"
        data-testid="appearance-error"
      />

      <div id="settings-card-background" :class="cardClass">
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

      <div id="settings-card-tile-preview" :class="cardClass" class="mt-6">
        <fieldset>
          <legend class="text-lg font-semibold text-gray-800 dark:text-gray-100">
            Document previews
          </legend>
          <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">
            How each dashboard tile shows the document's first page. Saves to your account
            automatically.
          </p>
          <div role="radiogroup" aria-label="Document previews" class="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-5">
            <button
              v-for="mode in TILE_PREVIEWS"
              :key="mode.value"
              type="button"
              role="radio"
              :aria-checked="selectedTilePreview === mode.value"
              :data-testid="`tile-${mode.value}`"
              :class="[
                'flex flex-col gap-1 rounded-lg border p-3 text-left transition cursor-pointer',
                selectedTilePreview === mode.value
                  ? 'border-violet-500 ring-2 ring-violet-500/30'
                  : 'border-gray-200 dark:border-gray-700/60 hover:border-gray-300 dark:hover:border-gray-600',
              ]"
              @click="selectTilePreview(mode.value)"
            >
              <span class="text-sm font-medium text-gray-700 dark:text-gray-200">{{ mode.text }}</span>
              <span class="text-xs text-gray-500 dark:text-gray-400">{{ mode.hint }}</span>
            </button>
          </div>
        </fieldset>
      </div>
    </section>

    <!-- Notifications tab -->
    <section
      id="settings-panel-notifications"
      v-show="tab === 'notifications'"
      role="tabpanel"
      data-testid="tab-notifications"
    >
      <AppErrorSummary
        v-if="notifError"
        :errors="[{ text: notifError }]"
        data-testid="notifications-error"
      />

      <div v-if="notifSaved" class="mb-6">
        <AppBanner variant="success" data-testid="notifications-saved">
          <p>Your notification settings have been saved.</p>
        </AppBanner>
      </div>

      <div id="settings-card-notifications" :class="cardClass">
        <form @submit.prevent="onNotificationsSubmit">
          <label class="flex items-center gap-2 py-1">
            <input
              v-model="notifEnabled"
              id="notifications-enabled"
              type="checkbox"
              class="form-checkbox"
              data-testid="notifications-enabled"
            />
            <span class="text-sm font-medium text-gray-700 dark:text-gray-300"
              >Enable push notifications</span
            >
          </label>
          <p class="text-sm text-gray-500 dark:text-gray-400 mb-5">
            Send push notifications to your devices via
            <a href="https://pushover.net" class="text-violet-500 underline">Pushover</a>. Enabling
            this requires a valid app token and user key.
          </p>

          <div class="space-y-5">
            <AppInput
              id="pushover-app-token"
              type="password"
              label="Pushover application token"
              autocomplete="off"
              :hint="
                notifAppTokenSet
                  ? 'Saved — leave blank to keep the current token.'
                  : 'The API token from your Pushover application.'
              "
              v-model="notifAppToken"
            />
            <AppInput
              id="pushover-user-key"
              type="password"
              label="Pushover user key"
              autocomplete="off"
              :hint="
                notifUserKeySet
                  ? 'Saved — leave blank to keep the current key.'
                  : 'Your Pushover user (or group) key.'
              "
              v-model="notifUserKey"
            />
            <AppInput
              id="pushover-device"
              label="Pushover device (optional)"
              autocomplete="off"
              hint="Limit notifications to a single device. Leave blank for all devices."
              v-model="notifDevice"
            />

            <AppCheckboxes
              id="notification-events"
              legend="Notify me about"
              legend-size="s"
              hint="Choose which document events trigger a notification."
              :items="notificationEventItems"
              v-model="notifEvents"
              small
            />

            <AppTextarea
              id="email-forward-addresses"
              label="Email addresses you forward documents from"
              :rows="4"
              autocomplete="off"
              hint="One per line (or comma-separated). Documents arriving from these addresses are attributed to you."
              v-model="notifAddresses"
            />
          </div>

          <div class="mt-6">
            <AppButton type="submit" :disabled="notifSaving">Save changes</AppButton>
          </div>
        </form>
      </div>
    </section>
  </div>
</template>
