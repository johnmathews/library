<script setup lang="ts">
/**
 * Saved views management (route `/saved-views`): lists the caller's saved
 * views and supports apply (navigate to the homepage with the saved query),
 * inline rename, delete, pin/unpin (pinned views become sidebar dashboards),
 * and up/down reorder. All state lives in the saved-views store; each mutation
 * calls the store, which keeps the list in sync with the REST API.
 */
import { computed, onMounted, ref } from 'vue'

import { useSavedViewsStore } from '@/stores/savedViews'
import type { SavedView } from '@/api/savedViews'
import { ApiError } from '@/api/client'
import { AppButton, AppInput, PageHeader } from '@/components/app'

const store = useSavedViewsStore()
const views = computed<SavedView[]>(() => store.views)

const loading = ref(true)
const loadError = ref(false)

async function load(): Promise<void> {
  loading.value = true
  loadError.value = false
  try {
    await store.load(true)
  } catch {
    loadError.value = true
  } finally {
    loading.value = false
  }
}

onMounted(load)

function errorText(error: unknown): string {
  return error instanceof ApiError && error.status !== 0
    ? error.detail
    : 'Something went wrong — check your connection and try again.'
}

// A single busy id disables that row's controls while a request is in flight.
const busyId = ref<number | null>(null)
const rowError = ref<{ id: number; message: string } | null>(null)

// --- Inline rename -----------------------------------------------------------

const editingId = ref<number | null>(null)
const editName = ref('')

function startEdit(view: SavedView): void {
  editingId.value = view.id
  editName.value = view.name
  rowError.value = null
}

function cancelEdit(): void {
  editingId.value = null
}

async function saveEdit(id: number): Promise<void> {
  if (!editName.value.trim() || busyId.value) return
  busyId.value = id
  rowError.value = null
  try {
    await store.update(id, { name: editName.value.trim() })
    editingId.value = null
  } catch (error) {
    rowError.value = { id, message: errorText(error) }
  } finally {
    busyId.value = null
  }
}

// --- Pin toggle --------------------------------------------------------------

async function togglePin(view: SavedView): Promise<void> {
  if (busyId.value) return
  busyId.value = view.id
  rowError.value = null
  try {
    await store.update(view.id, { pinned: !view.pinned })
  } catch (error) {
    rowError.value = { id: view.id, message: errorText(error) }
  } finally {
    busyId.value = null
  }
}

// --- Delete (two-step inline confirm) ----------------------------------------

const confirmingId = ref<number | null>(null)

function askDelete(id: number): void {
  confirmingId.value = id
  rowError.value = null
}

function cancelDelete(): void {
  confirmingId.value = null
}

async function confirmDelete(id: number): Promise<void> {
  if (busyId.value) return
  busyId.value = id
  rowError.value = null
  try {
    await store.remove(id)
    confirmingId.value = null
  } catch (error) {
    rowError.value = { id, message: errorText(error) }
  } finally {
    busyId.value = null
  }
}

// --- Reorder (up/down) -------------------------------------------------------
// Send the full reordered id list; the server rejects any set that isn't
// exactly the caller's current ids, so we always derive from the live list.

async function move(index: number, delta: number): Promise<void> {
  const target = index + delta
  if (busyId.value || target < 0 || target >= views.value.length) return
  const ids = views.value.map((view) => view.id)
  const moved = ids[index]!
  ids.splice(index, 1)
  ids.splice(target, 0, moved)
  busyId.value = moved
  rowError.value = null
  try {
    await store.reorder(ids)
  } catch (error) {
    rowError.value = { id: moved, message: errorText(error) }
  } finally {
    busyId.value = null
  }
}
</script>

<template>
  <div id="saved-views-view">
    <PageHeader title="Saved views" />

    <p v-if="loading" data-testid="saved-views-loading" class="text-gray-500 dark:text-gray-400">
      Loading…
    </p>
    <p
      v-else-if="loadError"
      data-testid="saved-views-error"
      class="text-red-600 dark:text-red-400"
    >
      Could not load saved views. Try again later.
    </p>
    <p
      v-else-if="views.length === 0"
      data-testid="saved-views-empty"
      class="text-gray-500 dark:text-gray-400"
    >
      No saved views yet. Set filters on the dashboard, then use “Save view” to keep them here.
    </p>

    <ul v-else class="space-y-2" data-testid="saved-views-list">
      <li
        v-for="(view, index) in views"
        :key="view.id"
        class="card p-4"
        data-testid="saved-view-row"
      >
        <!-- Inline rename form. -->
        <div v-if="editingId === view.id" class="space-y-2">
          <AppInput
            :id="`saved-view-edit-name-${view.id}`"
            v-model="editName"
            :testid="`rename-input-${view.id}`"
            label="Name"
            hide-label
          />
          <div class="flex gap-2">
            <AppButton
              variant="primary"
              size="sm"
              type="button"
              :data-testid="`rename-save-${view.id}`"
              :disabled="!editName.trim() || busyId === view.id"
              @click="saveEdit(view.id)"
            >
              Save
            </AppButton>
            <AppButton
              variant="secondary"
              size="sm"
              type="button"
              :data-testid="`rename-cancel-${view.id}`"
              @click="cancelEdit"
            >
              Cancel
            </AppButton>
          </div>
        </div>

        <!-- Read row. -->
        <div v-else class="flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0">
            <RouterLink
              :to="{ path: '/', query: view.filter_state }"
              :data-testid="`saved-view-name-${view.id}`"
              class="font-medium text-violet-600 dark:text-violet-300 hover:underline break-words"
            >
              {{ view.name }}
            </RouterLink>
            <span
              v-if="view.pinned"
              :data-testid="`saved-view-pinned-badge-${view.id}`"
              class="ml-2 text-xs rounded bg-violet-100 dark:bg-violet-500/20 text-violet-700 dark:text-violet-300 px-1.5 py-0.5"
            >
              Pinned
            </span>
          </div>

          <div class="flex flex-wrap items-center gap-2 shrink-0">
            <div class="flex items-center gap-1">
              <button
                type="button"
                class="btn-sm border-gray-200 dark:border-gray-700/60 text-gray-600 dark:text-gray-300 disabled:opacity-40"
                :data-testid="`view-up-${view.id}`"
                :disabled="index === 0 || busyId !== null"
                aria-label="Move up"
                @click="move(index, -1)"
              >
                ↑
              </button>
              <button
                type="button"
                class="btn-sm border-gray-200 dark:border-gray-700/60 text-gray-600 dark:text-gray-300 disabled:opacity-40"
                :data-testid="`view-down-${view.id}`"
                :disabled="index === views.length - 1 || busyId !== null"
                aria-label="Move down"
                @click="move(index, 1)"
              >
                ↓
              </button>
            </div>
            <AppButton
              variant="secondary"
              size="sm"
              type="button"
              :data-testid="`toggle-pin-${view.id}`"
              :disabled="busyId === view.id"
              @click="togglePin(view)"
            >
              {{ view.pinned ? 'Unpin' : 'Pin to sidebar' }}
            </AppButton>
            <AppButton
              variant="secondary"
              size="sm"
              type="button"
              :data-testid="`rename-view-${view.id}`"
              @click="startEdit(view)"
            >
              Rename
            </AppButton>
            <template v-if="confirmingId === view.id">
              <AppButton
                variant="warning"
                size="sm"
                type="button"
                :data-testid="`delete-confirm-${view.id}`"
                :disabled="busyId === view.id"
                @click="confirmDelete(view.id)"
              >
                Confirm delete
              </AppButton>
              <AppButton
                variant="secondary"
                size="sm"
                type="button"
                :data-testid="`delete-cancel-${view.id}`"
                @click="cancelDelete"
              >
                Cancel
              </AppButton>
            </template>
            <button
              v-else
              type="button"
              :data-testid="`delete-view-${view.id}`"
              class="btn-sm border-red-200 dark:border-red-900/60 text-red-600 dark:text-red-400"
              @click="askDelete(view.id)"
            >
              Delete
            </button>
          </div>
        </div>

        <p
          v-if="rowError && rowError.id === view.id"
          :data-testid="`saved-view-error-${view.id}`"
          class="text-sm text-red-600 dark:text-red-400 mt-2"
        >
          {{ rowError.message }}
        </p>
      </li>
    </ul>
  </div>
</template>
