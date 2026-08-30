<script setup lang="ts">
/**
 * Suggestions tab: the labeller's pending facet-value suggestion queue.
 *
 * `accept` is the only sanctioned path that widens the closed vocabulary: it
 * derives a clean key from `suggested_label`, creates the value, AND labels
 * the originating document in one call — so the owner sees the key that is
 * about to enter the vocabulary (via `slugify`, shared with `FacetsPanel`'s
 * create-value form) before committing to it. The server remains the judge
 * of the real key: its 409 (derived key already exists) and 422 (nothing
 * usable in the label) are rendered verbatim from `ApiError.detail`, never
 * re-worded — the preview here is for orientation only.
 *
 * Loads lazily exactly as `AdminMetadataPanel`/`FacetsPanel` do:
 * `watch(() => props.active, ..., { immediate: true })` with a `loaded` flag,
 * fetching on the first moment `active` is true.
 */
import { ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import {
  acceptSuggestion,
  dismissSuggestion,
  listSuggestions,
  type FacetSuggestion,
} from '@/api/facets'
import { ApiError } from '@/api/client'
import { slugify } from '@/utils/slugify'

defineOptions({ name: 'SuggestionsPanel' })
const props = defineProps<{ active: boolean }>()

const suggestions = ref<FacetSuggestion[]>([])
const loading = ref(false)
const loaded = ref(false)
const loadError = ref<string | null>(null)

async function load(): Promise<void> {
  loading.value = true
  loadError.value = null
  try {
    suggestions.value = await listSuggestions()
    loaded.value = true
  } catch {
    loadError.value = 'Could not load the suggestions. Try refreshing the page.'
  } finally {
    loading.value = false
  }
}

watch(
  () => props.active,
  (isActive) => {
    if (!isActive) return
    if (!loaded.value && !loading.value) void load()
  },
  { immediate: true },
)

const pendingIds = ref<Set<number>>(new Set())
const rowError = ref<Record<number, string>>({})

function setPending(id: number, pending: boolean): void {
  const next = new Set(pendingIds.value)
  if (pending) next.add(id)
  else next.delete(id)
  pendingIds.value = next
}

function isPending(id: number): boolean {
  return pendingIds.value.has(id)
}

function setRowError(id: number, message: string | null): void {
  const next = { ...rowError.value }
  if (message) next[id] = message
  else delete next[id]
  rowError.value = next
}

/** After accept or dismiss: reload the queue rather than patching it locally,
 * so it stays truthful about what the server still has pending. */
async function afterMutation(): Promise<void> {
  await load()
}

async function onAccept(id: number): Promise<void> {
  setPending(id, true)
  setRowError(id, null)
  try {
    await acceptSuggestion(id)
    await afterMutation()
  } catch (err) {
    setRowError(id, err instanceof ApiError ? err.detail : 'Could not accept the suggestion. Try again.')
  } finally {
    setPending(id, false)
  }
}

async function onDismiss(id: number): Promise<void> {
  setPending(id, true)
  setRowError(id, null)
  try {
    await dismissSuggestion(id)
    await afterMutation()
  } catch (err) {
    setRowError(id, err instanceof ApiError ? err.detail : 'Could not dismiss the suggestion. Try again.')
  } finally {
    setPending(id, false)
  }
}
</script>

<template>
  <div class="card p-6 @container">
    <h2 class="mb-4 text-lg font-semibold text-gray-800 dark:text-gray-100">Suggestions</h2>

    <p v-if="loading" class="text-sm text-gray-500 dark:text-gray-400" data-testid="suggestions-loading">
      Loading the suggestions…
    </p>
    <div
      v-else-if="loadError"
      role="alert"
      class="border-l-4 border-red-500 bg-red-50 dark:bg-red-500/10 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-300"
      data-testid="suggestions-error"
    >
      {{ loadError }}
    </div>
    <p
      v-else-if="suggestions.length === 0"
      class="text-sm text-gray-500 dark:text-gray-400"
      data-testid="suggestions-empty"
    >
      No suggestions pending.
    </p>

    <ul v-else class="divide-y divide-gray-100 dark:divide-gray-700/60">
      <li
        v-for="suggestion in suggestions"
        :key="suggestion.id"
        class="py-3"
        :class="{ 'opacity-60': isPending(suggestion.id) }"
        :data-testid="`suggestion-${suggestion.id}`"
      >
        <div class="flex flex-col gap-2 @md:flex-row @md:items-start @md:justify-between">
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-1.5">
              <code class="text-xs text-gray-400 dark:text-gray-500">{{ suggestion.facet }}</code>
              <span class="font-medium text-gray-800 dark:text-gray-100">
                {{ suggestion.suggested_label }}
              </span>
              <code
                class="text-xs text-violet-600 dark:text-violet-400"
                :data-testid="`suggestion-${suggestion.id}-key`"
              >
                {{ slugify(suggestion.suggested_label) }}
              </code>
            </div>
            <p v-if="suggestion.reason" class="text-sm text-gray-600 dark:text-gray-300">
              {{ suggestion.reason }}
            </p>
            <RouterLink
              :to="{ name: 'document-detail', params: { id: suggestion.document_id } }"
              class="text-xs text-violet-600 hover:underline dark:text-violet-400"
              :data-testid="`suggestion-${suggestion.id}-document`"
            >
              View document
            </RouterLink>
          </div>

          <div class="flex shrink-0 items-center gap-2">
            <button
              type="button"
              class="btn-xs bg-violet-500 text-white hover:bg-violet-600"
              :disabled="isPending(suggestion.id)"
              :data-testid="`suggestion-${suggestion.id}-accept`"
              @click="onAccept(suggestion.id)"
            >
              Accept
            </button>
            <button
              type="button"
              class="btn-xs border border-gray-200 dark:border-gray-700/60 text-gray-700 dark:text-gray-300"
              :disabled="isPending(suggestion.id)"
              :data-testid="`suggestion-${suggestion.id}-dismiss`"
              @click="onDismiss(suggestion.id)"
            >
              Dismiss
            </button>
          </div>
        </div>

        <p
          v-if="rowError[suggestion.id]"
          role="alert"
          class="mt-1 text-xs text-red-600 dark:text-red-400"
          :data-testid="`suggestion-${suggestion.id}-error`"
        >
          {{ rowError[suggestion.id] }}
        </p>
      </li>
    </ul>
  </div>
</template>
