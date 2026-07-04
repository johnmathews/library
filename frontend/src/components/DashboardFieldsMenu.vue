<script setup lang="ts">
/**
 * Dashboard "Fields" button: a mosaic-styled button that opens a popover to
 * toggle and reorder the metadata fields shown on document cards. Changes
 * persist immediately via PUT /api/settings and are reflected app-wide through
 * the auth store (same mechanism as Settings → Dashboard). Overlay behaviour
 * (Escape closes + returns focus, outside-mousedown closes, z-index) comes from
 * AppPopover.
 */
import { ref, watch } from 'vue'
import AppPopover from './app/AppPopover.vue'
import DashboardFieldsEditor from './DashboardFieldsEditor.vue'
import { updateSettings, type DashboardField } from '@/api/settings'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()

const open = ref(false)
const errorMessage = ref<string | null>(null)
// Remount the editor each time the popover opens so it re-seeds from the
// current stored order.
const editorKey = ref(0)

async function persist(fields: DashboardField[]): Promise<void> {
  errorMessage.value = null
  try {
    const result = await updateSettings({ dashboard_fields: fields })
    auth.applyPreferences(result)
  } catch {
    errorMessage.value = 'Could not save. Try again.'
  }
}

// Re-seed the editor and clear any stale error each time the popover opens.
watch(open, (isOpen) => {
  if (isOpen) {
    errorMessage.value = null
    editorKey.value += 1
  }
})
</script>

<template>
  <AppPopover
    :open="open"
    align="right"
    :panel-attrs="{ role: 'dialog', 'aria-label': 'Card fields', 'data-testid': 'dashboard-fields-panel' }"
    panel-class="absolute top-full mt-1 w-64 max-w-[calc(100vw-1rem)] p-3"
    @update:open="open = $event"
  >
    <template #trigger="{ open: isOpen, toggle, triggerRef }">
      <button
        :ref="triggerRef"
        type="button"
        data-testid="dashboard-fields-button"
        class="inline-flex items-center gap-1.5 rounded-md border border-gray-300 bg-white px-3 py-1 text-sm text-gray-700 transition-colors hover:bg-violet-50 hover:text-violet-700 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-violet-400/10"
        :aria-expanded="isOpen"
        aria-haspopup="dialog"
        @click="toggle"
      >
        <svg class="h-4 w-4 fill-current opacity-70" viewBox="0 0 20 20" aria-hidden="true">
          <path d="M3 4h4v12H3V4zm5 0h4v12H8V4zm5 0h4v12h-4V4z" />
        </svg>
        Fields
      </button>
    </template>

    <p class="mb-2 text-xs font-medium uppercase tracking-wide text-gray-400">Card fields</p>
    <DashboardFieldsEditor
      :key="editorKey"
      :model-value="auth.dashboardFields"
      @update:model-value="persist"
    />
    <p v-if="errorMessage" class="mt-2 text-sm text-red-500" data-testid="dashboard-fields-error">
      {{ errorMessage }}
    </p>
  </AppPopover>
</template>
