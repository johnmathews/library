<script setup lang="ts" generic="T extends TaxonomyRow">
/**
 * Shared CRUD card for a taxonomy entity (senders / recipients / kinds),
 * driven by a {@link TaxonomyDescriptor}. Owns list load, per-row pending/error
 * state, create, rename (branching on `descriptor.hasMerge`) and delete-with-
 * reassign. Loads lazily: the parent flips `active` true when the Metadata tab
 * opens and this panel fetches on the first false→true transition.
 */
import { ref, watch, type Ref } from 'vue'
import { AppBadge, AppButton, AppInput, AppSelect } from '@/components/app'
import type { SelectItem } from '@/components/app'
import { ApiError } from '@/api/client'
import { refreshTaxonomyOptions } from '@/composables/taxonomyOptions'
import type {
  TaxonomyDescriptor,
  TaxonomyKey,
  TaxonomyMergeTarget,
  TaxonomyRow,
} from './taxonomyCrud'

const props = defineProps<{
  descriptor: TaxonomyDescriptor<T>
  active: boolean
}>()

const cardClass = 'card p-6'

const items = ref<T[]>([]) as Ref<T[]>
const loading = ref(false)
const loaded = ref(false)
const error = ref<string | null>(null)
// Per-row action state, keyed by the row key (id or slug).
const pendingKeys = ref<Set<TaxonomyKey>>(new Set())
const rowError = ref<Record<string, string>>({})
// Inline rename state: at most one row in edit mode at a time.
const renameKey = ref<TaxonomyKey | null>(null)
const renameValue = ref('')
// When a rename hits a 409 (id-entities), the proposed merge target.
const mergeTarget = ref<TaxonomyMergeTarget | null>(null)
// Inline delete state: at most one row in confirm mode at a time.
const deleteKey = ref<TaxonomyKey | null>(null)
// Selected reassign target: '' means "None (clear)", otherwise a target key.
const reassignValue = ref('')
// Create control (above the list).
const createValue = ref('')
const creating = ref(false)
const createError = ref<string | null>(null)

const keyOf = (row: T): TaxonomyKey => props.descriptor.keyOf(row)

function setPending(key: TaxonomyKey, pending: boolean): void {
  const next = new Set(pendingKeys.value)
  if (pending) next.add(key)
  else next.delete(key)
  pendingKeys.value = next
}

function setError(key: TaxonomyKey, message: string | null): void {
  const next = { ...rowError.value }
  if (message) next[key] = message
  else delete next[key]
  rowError.value = next
}

/** Reassign options for a row: every other row, plus "None (clear)". */
function reassignItems(row: T): SelectItem[] {
  const rowKey = keyOf(row)
  const others = items.value
    .filter((other) => keyOf(other) !== rowKey)
    .map((other) => ({
      value: String(keyOf(other)),
      text: `${other.name} (${other.document_count})`,
    }))
  return [{ value: '', text: props.descriptor.clearText }, ...others]
}

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    items.value = await props.descriptor.list()
    loaded.value = true
  } catch {
    error.value = `Could not load ${props.descriptor.testid}s. Try refreshing the page.`
  } finally {
    loading.value = false
  }
}

/** After any successful mutation: reload the list and refresh the shared
 * taxonomy cache so document dropdowns/filters elsewhere reflect the change. */
async function afterMutation(): Promise<void> {
  await load()
  void refreshTaxonomyOptions()
}

function startRename(row: T): void {
  cancelDelete()
  renameKey.value = keyOf(row)
  renameValue.value = row.name
  mergeTarget.value = null
  setError(keyOf(row), null)
}

function cancelRename(): void {
  renameKey.value = null
  renameValue.value = ''
  mergeTarget.value = null
}

/** Save a rename. For id-entities a 409 collision (without `merge`) reveals the
 * merge prompt instead of erroring; for kinds (no merge) it is a row error. */
async function saveRename(row: T, merge = false): Promise<void> {
  const key = keyOf(row)
  const name = renameValue.value.trim()
  if (!name) {
    setError(key, 'Enter a name.')
    return
  }
  setPending(key, true)
  setError(key, null)
  try {
    await props.descriptor.rename(key, name, merge)
    cancelRename()
    await afterMutation()
  } catch (err) {
    if (
      props.descriptor.hasMerge &&
      props.descriptor.readMergeBody &&
      err instanceof ApiError &&
      err.status === 409 &&
      err.body &&
      !merge
    ) {
      mergeTarget.value = props.descriptor.readMergeBody(err.body)
    } else {
      setError(
        key,
        err instanceof ApiError
          ? err.detail
          : `Could not rename the ${props.descriptor.noun}. Try again.`,
      )
    }
  } finally {
    setPending(key, false)
  }
}

function startDelete(row: T): void {
  cancelRename()
  deleteKey.value = keyOf(row)
  reassignValue.value = ''
  setError(keyOf(row), null)
}

function cancelDelete(): void {
  deleteKey.value = null
  reassignValue.value = ''
}

/** Confirm a delete. In-use rows reassign (to the chosen key, or null to
 * clear); zero-document rows delete outright. */
async function confirmDelete(row: T): Promise<void> {
  const key = keyOf(row)
  setPending(key, true)
  setError(key, null)
  try {
    if (row.document_count > 0) {
      const chosen = props.descriptor.parseReassign(reassignValue.value)
      await props.descriptor.remove(key, chosen)
    } else {
      await props.descriptor.remove(key)
    }
    cancelDelete()
    await afterMutation()
  } catch (err) {
    setError(
      key,
      err instanceof ApiError
        ? err.detail
        : `Could not delete the ${props.descriptor.noun}. Try again.`,
    )
  } finally {
    setPending(key, false)
  }
}

async function onCreate(): Promise<void> {
  const name = createValue.value.trim()
  if (!name || creating.value) return
  creating.value = true
  createError.value = null
  try {
    await props.descriptor.create(name)
    createValue.value = ''
    await afterMutation()
  } catch (err) {
    createError.value =
      err instanceof ApiError
        ? err.detail
        : `Could not create the ${props.descriptor.noun}. Try again.`
  } finally {
    creating.value = false
  }
}

// Load lazily on the first false→true `active` transition (first Metadata open).
watch(
  () => props.active,
  (isActive) => {
    if (!isActive) return
    if (!loaded.value && !loading.value) void load()
  },
)
</script>

<template>
  <div :class="cardClass">
    <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-4">
      {{ descriptor.heading }}
    </h2>

    <!-- Create -->
    <div class="mb-4">
      <label
        :for="`${descriptor.testid}-create-input`"
        class="block text-xs font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-1"
      >
        {{ descriptor.addLabel }}
      </label>
      <div class="flex items-end gap-2">
        <input
          :id="`${descriptor.testid}-create-input`"
          v-model="createValue"
          type="text"
          autocomplete="off"
          class="form-input flex-1"
          :data-testid="`${descriptor.testid}-create-input`"
          @keyup.enter="onCreate()"
        />
        <AppButton
          type="button"
          :data-testid="`${descriptor.testid}-create-button`"
          :disabled="creating"
          @click="onCreate()"
        >
          {{ creating ? 'Adding…' : 'Add' }}
        </AppButton>
      </div>
      <p
        v-if="createError"
        :data-testid="`${descriptor.testid}-create-error`"
        class="mt-1 text-xs text-red-600 dark:text-red-400"
      >
        {{ createError }}
      </p>
    </div>

    <p
      v-if="loading"
      :data-testid="`${descriptor.testid}s-loading`"
      class="text-sm text-gray-500 dark:text-gray-400"
    >
      Loading {{ descriptor.testid }}s…
    </p>
    <div
      v-else-if="error"
      :data-testid="`${descriptor.testid}s-error`"
      role="alert"
      class="border-l-4 border-red-500 bg-red-50 dark:bg-red-500/10 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-300"
    >
      {{ error }}
    </div>
    <p
      v-else-if="items.length === 0"
      :data-testid="`${descriptor.testid}s-empty`"
      class="text-sm text-gray-500 dark:text-gray-400"
    >
      No {{ descriptor.testid }}s yet.
    </p>
    <ul
      v-else
      class="divide-y divide-gray-100 dark:divide-gray-700/60"
      :data-testid="`${descriptor.testid}-list`"
    >
      <li
        v-for="row in items"
        :key="keyOf(row)"
        :data-testid="`${descriptor.testid}-row-${keyOf(row)}`"
        class="py-3"
      >
        <!-- Display row -->
        <div v-if="renameKey !== keyOf(row)" class="flex items-center justify-between gap-3">
          <span class="min-w-0 truncate font-medium text-gray-800 dark:text-gray-100">
            {{ row.name }}
          </span>
          <div class="flex shrink-0 items-center gap-2">
            <AppBadge colour="grey">{{ row.document_count }} docs</AppBadge>
            <button
              type="button"
              :data-testid="`${descriptor.testid}-rename`"
              :disabled="pendingKeys.has(keyOf(row))"
              class="rounded-md border border-gray-200 dark:border-gray-700/60 px-2.5 py-1 text-xs font-medium text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50 cursor-pointer"
              @click="startRename(row)"
            >
              Rename
            </button>
            <button
              type="button"
              :data-testid="`${descriptor.testid}-delete`"
              :disabled="pendingKeys.has(keyOf(row))"
              class="rounded-md border border-red-200 dark:border-red-500/40 px-2.5 py-1 text-xs font-medium text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-500/10 disabled:opacity-50 cursor-pointer"
              @click="startDelete(row)"
            >
              Delete
            </button>
          </div>
        </div>

        <!-- Rename editor -->
        <div v-else class="space-y-2">
          <div class="flex items-end gap-2">
            <div class="flex-1">
              <AppInput
                :id="`${descriptor.testid}-rename-input-${keyOf(row)}`"
                v-model="renameValue"
                :label="descriptor.renameLabel"
                autocomplete="off"
                :data-testid="`${descriptor.testid}-rename-input`"
                @keyup.enter="saveRename(row)"
              />
            </div>
            <AppButton
              type="button"
              :data-testid="`${descriptor.testid}-rename-save`"
              :disabled="pendingKeys.has(keyOf(row))"
              @click="saveRename(row)"
            >
              Save
            </AppButton>
            <AppButton
              type="button"
              variant="secondary"
              :data-testid="`${descriptor.testid}-rename-cancel`"
              @click="cancelRename()"
            >
              Cancel
            </AppButton>
          </div>

          <!-- Merge prompt (id-entities only; kinds have hasMerge=false) -->
          <div
            v-if="descriptor.hasMerge && mergeTarget"
            :data-testid="`${descriptor.testid}-merge-warning`"
            role="alert"
            class="border-l-4 border-yellow-500 bg-yellow-50 dark:bg-yellow-500/10 rounded-lg px-3 py-2 text-sm text-gray-700 dark:text-gray-200"
          >
            <p>
              '{{ row.name }}' will be merged into '{{ mergeTarget.target_name }}'
              ({{ mergeTarget.target_document_count }} documents) and removed.
            </p>
            <AppButton
              type="button"
              class="mt-2"
              :data-testid="`${descriptor.testid}-merge-confirm`"
              :disabled="pendingKeys.has(keyOf(row))"
              @click="saveRename(row, true)"
            >
              Merge and remove
            </AppButton>
          </div>
        </div>

        <!-- Delete confirm / reassign -->
        <div
          v-if="deleteKey === keyOf(row)"
          :data-testid="`${descriptor.testid}-delete-confirm-box`"
          class="mt-2 border-l-4 border-red-500 bg-red-50 dark:bg-red-500/10 rounded-lg px-3 py-2 text-sm text-gray-700 dark:text-gray-200"
        >
          <template v-if="row.document_count > 0">
            <p class="mb-2">
              '{{ row.name }}' still has {{ row.document_count }} documents. Choose where to move
              them (or clear the {{ descriptor.noun }}) before deleting.
            </p>
            <AppSelect
              :id="`${descriptor.testid}-reassign-${keyOf(row)}`"
              v-model="reassignValue"
              label="Reassign documents to"
              :items="reassignItems(row)"
              :data-testid="`${descriptor.testid}-reassign-select`"
            />
          </template>
          <p v-else class="mb-2">Delete '{{ row.name }}'? This cannot be undone.</p>
          <div class="mt-2 flex gap-2">
            <AppButton
              type="button"
              :data-testid="`${descriptor.testid}-delete-confirm`"
              :disabled="pendingKeys.has(keyOf(row))"
              @click="confirmDelete(row)"
            >
              Delete
            </AppButton>
            <AppButton
              type="button"
              variant="secondary"
              :data-testid="`${descriptor.testid}-delete-cancel`"
              @click="cancelDelete()"
            >
              Cancel
            </AppButton>
          </div>
        </div>

        <p
          v-if="rowError[keyOf(row)]"
          :data-testid="`${descriptor.testid}-error-${keyOf(row)}`"
          class="mt-1 text-xs text-red-600 dark:text-red-400"
        >
          {{ rowError[keyOf(row)] }}
        </p>
      </li>
    </ul>
  </div>
</template>
