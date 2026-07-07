<script setup lang="ts">
/**
 * Dashboard "Save view" control: a mosaic-styled button that opens a popover to
 * name and save the current filter/search state as a saved view, optionally
 * pinning it to the sidebar as a custom dashboard. The popover also lists the
 * caller's existing views for one-click apply (navigate home with the saved
 * query). Overlay behaviour comes from AppPopover; the list stays in sync via
 * the saved-views store.
 *
 * `filterState` is the current homepage query (buildDocumentQuery(applied)),
 * passed in by DocumentListView so a save captures the exact live state.
 */
import { computed, ref, watch } from 'vue'
import { useRouter, type LocationQueryRaw } from 'vue-router'
import AppPopover from './app/AppPopover.vue'
import { useSavedViewsStore } from '@/stores/savedViews'
import { useFlashStore } from '@/stores/flash'

const props = defineProps<{
  /** The current homepage query to persist — output of buildDocumentQuery. */
  filterState: LocationQueryRaw
}>()

const router = useRouter()
const store = useSavedViewsStore()
const flash = useFlashStore()

const open = ref(false)
const name = ref('')
const pinned = ref(false)
const saving = ref(false)
const errorMessage = ref<string | null>(null)

const views = computed(() => store.views)

// Load the list lazily the first time the popover opens, and reset the form.
watch(open, (isOpen) => {
  if (isOpen) {
    errorMessage.value = null
    name.value = ''
    pinned.value = false
    void store.load()
  }
})

async function submit(): Promise<void> {
  if (!name.value.trim() || saving.value) return
  saving.value = true
  errorMessage.value = null
  try {
    await store.create({
      name: name.value.trim(),
      filter_state: props.filterState,
      pinned: pinned.value,
    })
    open.value = false
    flash.set('View saved')
  } catch {
    errorMessage.value = 'Could not save. Try again.'
  } finally {
    saving.value = false
  }
}

function applyView(query: LocationQueryRaw): void {
  open.value = false
  void router.push({ path: '/', query })
}
</script>

<template>
  <AppPopover
    :open="open"
    align="none"
    :panel-attrs="{ role: 'dialog', 'aria-label': 'Save view', 'data-testid': 'save-view-panel' }"
    panel-class="p-3 fixed inset-x-2 bottom-2 max-h-[80vh] overflow-y-auto sm:absolute sm:inset-x-auto sm:bottom-auto sm:right-0 sm:top-full sm:mt-1 sm:w-72 sm:max-w-[calc(100vw-1rem)] sm:max-h-none sm:overflow-visible"
    @update:open="open = $event"
  >
    <template #trigger="{ open: isOpen, toggle, triggerRef }">
      <button
        :ref="triggerRef"
        type="button"
        data-testid="save-view-menu"
        class="inline-flex items-center gap-1.5 rounded-md border border-gray-300 bg-white px-3 py-1 text-sm text-gray-700 transition-colors hover:bg-violet-50 hover:text-violet-700 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-violet-400/10"
        :aria-expanded="isOpen"
        aria-haspopup="dialog"
        @click="toggle"
      >
        <svg class="h-4 w-4 fill-current opacity-70" viewBox="0 0 20 20" aria-hidden="true">
          <path d="M5 2h8l4 4v12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1h1zm1 0v5h6V2H6zm4 8a2 2 0 1 0 0 4 2 2 0 0 0 0-4z" />
        </svg>
        Save view
      </button>
    </template>

    <p class="mb-2 text-xs font-medium uppercase tracking-wide text-gray-400">Save current filters</p>
    <form class="space-y-2" @submit.prevent="submit">
      <div>
        <label
          for="save-view-name"
          class="block text-xs font-medium uppercase tracking-wide text-gray-400 mb-1"
        >
          View name
        </label>
        <input
          id="save-view-name"
          v-model="name"
          type="text"
          data-testid="save-view-name"
          autocomplete="off"
          class="form-input w-full text-sm"
          placeholder="e.g. Unpaid invoices"
        />
      </div>
      <label class="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300">
        <input
          v-model="pinned"
          type="checkbox"
          class="form-checkbox"
          data-testid="save-view-pinned"
        />
        Pin to sidebar
      </label>
      <button
        type="submit"
        data-testid="save-view-submit"
        class="btn-sm bg-violet-500 hover:bg-violet-600 text-white disabled:opacity-60"
        :disabled="!name.trim() || saving"
      >
        {{ saving ? 'Saving…' : 'Save' }}
      </button>
      <p v-if="errorMessage" class="text-sm text-red-500" data-testid="save-view-error">
        {{ errorMessage }}
      </p>
    </form>

    <div v-if="views.length" class="mt-3 border-t border-gray-200 dark:border-gray-700/60 pt-2">
      <p class="mb-1 text-xs font-medium uppercase tracking-wide text-gray-400">Your views</p>
      <ul class="space-y-0.5" data-testid="save-view-list">
        <li v-for="view in views" :key="view.id">
          <button
            type="button"
            :data-testid="`apply-view-${view.id}`"
            class="flex w-full items-center gap-2 rounded px-2 py-1 text-left text-sm text-gray-700 hover:bg-violet-50 hover:text-violet-700 dark:text-gray-200 dark:hover:bg-violet-400/10"
            @click="applyView(view.filter_state)"
          >
            <span class="truncate">{{ view.name }}</span>
            <span v-if="view.pinned" class="ml-auto text-xs text-violet-500" aria-hidden="true">📌</span>
          </button>
        </li>
      </ul>
    </div>
  </AppPopover>
</template>
