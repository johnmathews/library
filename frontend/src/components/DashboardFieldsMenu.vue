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
    align="none"
    :panel-attrs="{ role: 'dialog', 'aria-label': 'Fields', 'data-testid': 'dashboard-fields-panel' }"
    panel-class="p-3 fixed inset-x-2 bottom-2 max-h-[80vh] overflow-y-auto sm:absolute sm:inset-x-auto sm:bottom-auto sm:right-0 sm:top-full sm:mt-1 sm:w-64 sm:max-w-[calc(100vw-1rem)] sm:max-h-none sm:overflow-visible"
    @update:open="open = $event"
  >
    <template #trigger="{ open: isOpen, toggle, triggerRef }">
      <button
        :ref="triggerRef"
        type="button"
        data-testid="dashboard-fields-button"
        class="inline-flex items-center gap-1.5 rounded-sm border border-control-border bg-panel px-3 py-1 text-sm font-semibold text-ink transition-colors hover:bg-tile"
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

    <p class="mb-2 text-[13px] font-semibold text-ink-secondary">Fields</p>
    <DashboardFieldsEditor
      :key="editorKey"
      :model-value="auth.dashboardFields"
      @update:model-value="persist"
    />
    <p v-if="errorMessage" class="mt-2 text-sm text-error-ink" data-testid="dashboard-fields-error">
      {{ errorMessage }}
    </p>
  </AppPopover>
</template>
