<script setup lang="ts">
/**
 * Dashboard "Fields" button: a mosaic-styled button that opens a popover to
 * toggle and reorder the metadata fields shown on document cards. Changes
 * persist immediately via PUT /api/settings and are reflected app-wide through
 * the auth store (same mechanism as Settings → Dashboard). Closes on Escape
 * (focus returns to the button) and on outside mousedown, mirroring FilterPill.
 */
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import DashboardFieldsEditor from './DashboardFieldsEditor.vue'
import { updateSettings, type DashboardField } from '@/api/settings'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()

const open = ref(false)
const errorMessage = ref<string | null>(null)
const root = ref<HTMLElement | null>(null)
const button = ref<HTMLButtonElement | null>(null)
// Remount the editor each time the popover opens so it re-seeds from the
// current stored order.
const editorKey = ref(0)

function toggle(): void {
  open.value = !open.value
}

function close(): void {
  open.value = false
}

function onEscape(): void {
  close()
  button.value?.focus()
}

function onOutsideMousedown(event: MouseEvent): void {
  if (root.value && event.target instanceof Node && !root.value.contains(event.target)) {
    close()
  }
}

async function persist(fields: DashboardField[]): Promise<void> {
  errorMessage.value = null
  try {
    const result = await updateSettings({ dashboard_fields: fields })
    auth.applyPreferences(result)
  } catch {
    errorMessage.value = 'Could not save. Try again.'
  }
}

watch(open, (isOpen) => {
  if (isOpen) {
    errorMessage.value = null
    editorKey.value += 1
    void nextTick()
    document.addEventListener('mousedown', onOutsideMousedown)
  } else {
    document.removeEventListener('mousedown', onOutsideMousedown)
  }
})

onBeforeUnmount(() => document.removeEventListener('mousedown', onOutsideMousedown))
</script>

<template>
  <div ref="root" class="relative inline-flex" @keydown.escape.stop="onEscape">
    <button
      ref="button"
      type="button"
      data-testid="dashboard-fields-button"
      class="inline-flex items-center gap-1.5 rounded-md border border-gray-300 bg-white px-3 py-1 text-sm text-gray-700 transition-colors hover:bg-violet-50 hover:text-violet-700 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-violet-400/10"
      :aria-expanded="open"
      aria-haspopup="dialog"
      @click="toggle"
    >
      <svg class="h-4 w-4 fill-current opacity-70" viewBox="0 0 20 20" aria-hidden="true">
        <path d="M3 4h4v12H3V4zm5 0h4v12H8V4zm5 0h4v12h-4V4z" />
      </svg>
      Fields
    </button>

    <Transition
      enter-active-class="transition ease-out duration-150"
      enter-from-class="opacity-0 -translate-y-1"
      enter-to-class="opacity-100 translate-y-0"
      leave-active-class="transition ease-out duration-150"
      leave-from-class="opacity-100 translate-y-0"
      leave-to-class="opacity-0 -translate-y-1"
    >
      <div
        v-if="open"
        role="dialog"
        aria-label="Card fields"
        data-testid="dashboard-fields-panel"
        class="absolute right-0 top-full z-20 mt-1 w-64 max-w-[calc(100vw-1rem)] rounded-lg border border-gray-200 bg-white p-3 shadow-lg dark:border-gray-700/60 dark:bg-gray-800"
      >
        <p class="mb-2 text-xs font-medium uppercase tracking-wide text-gray-400">Card fields</p>
        <DashboardFieldsEditor
          :key="editorKey"
          :model-value="auth.dashboardFields"
          @update:model-value="persist"
        />
        <p v-if="errorMessage" class="mt-2 text-sm text-red-500" data-testid="dashboard-fields-error">
          {{ errorMessage }}
        </p>
      </div>
    </Transition>
  </div>
</template>
