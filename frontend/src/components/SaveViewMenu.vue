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
    align="auto"
    :panel-attrs="{ role: 'dialog', 'aria-label': 'Save view', 'data-testid': 'save-view-panel' }"
    panel-class="p-3 fixed max-sm:inset-x-2 bottom-2 max-h-[80vh] overflow-y-auto sm:absolute sm:bottom-auto sm:top-full sm:mt-1 sm:w-72 sm:max-w-[calc(100vw-1rem)] sm:max-h-none sm:overflow-visible"
    @update:open="open = $event"
  >
    <template #trigger="{ open: isOpen, toggle, triggerRef }">
      <button
        :ref="triggerRef"
        type="button"
        data-testid="save-view-menu"
        class="inline-flex items-center gap-1.5 rounded-sm border border-control-border bg-panel px-3 py-1 text-sm font-semibold text-ink transition-colors hover:bg-tile"
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

    <p class="mb-2 text-[13px] font-semibold text-ink-secondary">Save current filters</p>
    <form class="space-y-2" @submit.prevent="submit">
      <div>
        <label
          for="save-view-name"
          class="block text-[13px] font-semibold text-ink-secondary mb-1"
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
      <label class="flex items-center gap-2 text-sm text-ink-secondary">
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
        class="btn-sm bg-brand-fill text-on-brand hover:brightness-110 disabled:opacity-60"
        :disabled="!name.trim() || saving"
      >
        {{ saving ? 'Saving…' : 'Save' }}
      </button>
      <p v-if="errorMessage" class="text-sm text-error-ink" data-testid="save-view-error">
        {{ errorMessage }}
      </p>
    </form>

    <div v-if="views.length" class="mt-3 border-t border-line pt-2">
      <p class="mb-1 text-[13px] font-semibold text-ink-secondary">Your views</p>
      <ul class="space-y-0.5" data-testid="save-view-list">
        <li v-for="view in views" :key="view.id">
          <button
            type="button"
            :data-testid="`apply-view-${view.id}`"
            class="flex w-full items-center gap-2 rounded-sm px-2 py-1 text-left text-sm text-ink hover:bg-tile"
            @click="applyView(view.filter_state)"
          >
            <span class="truncate">{{ view.name }}</span>
            <span v-if="view.pinned" class="ml-auto text-xs text-ink-secondary" aria-hidden="true">📌</span>
          </button>
        </li>
      </ul>
    </div>
  </AppPopover>
</template>
