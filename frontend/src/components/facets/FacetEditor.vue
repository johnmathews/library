<script setup lang="ts">
/**
 * Edit one document's facet labels (docs/facets.md).
 *
 * Renders EVERY facet in the controlled vocabulary, including ones with no
 * values yet, as a disabled select with a hint. That is deliberately the
 * opposite of FacetFilterBar (which omits an empty facet entirely): there the
 * empty select would just be noise, but here the owner needs to SEE that a
 * facet such as `vehicle` exists before they can ask for a value to be added
 * to it.
 *
 * Within a select, values are ordered by label rather than by the vocabulary's
 * stored `ordinal`, matching FacetFilterBar.vue — `category`'s nineteen entries
 * are unfindable in seed-insertion order. The sort is display-only on purpose:
 * `load_vocabulary`'s canonical order still feeds the LLM labelling prompt and
 * the `/vocabulary` panel, which exists to expose that very ordinal.
 *
 * Only changed facets are sent to the server, and a cleared facet is sent as
 * an explicit `null` so the backend removes the label rather than silently
 * leaving the previous value in place. A failed save leaves the edit in the
 * draft (never silently discarded) and shows an error.
 *
 * The draft is never discarded silently: neither by a failed save, nor by a
 * label fetch that resolves after the user has already chosen a value (see
 * `touched` below).
 */
import { computed, ref, watch } from 'vue'
import { updateDocumentLabels, type FacetRef } from '@/api/facets'
import { AppButton } from '@/components/app'

const props = defineProps<{
  documentId: number
  facets: FacetRef[]
  labels: Record<string, string>
}>()

const emit = defineEmits<{ saved: [Record<string, string>] }>()

/** Every facet, empty ones included (see above), each with its values in
 * label order. */
const ordered = computed<FacetRef[]>(() =>
  props.facets.map((facet) => ({
    ...facet,
    values: [...facet.values].sort((a, b) => a.label.localeCompare(b.label)),
  })),
)

const draft = ref<Record<string, string>>({ ...props.labels })
const saving = ref(false)
const error = ref<string | null>(null)

/**
 * Whether the user has edited the draft since it was last hydrated from the
 * server. This exists because `labels` can arrive AFTER a selection.
 *
 * DocumentDetailView feeds this component from two independent fetches —
 * `facets` from `fetchFacets` in onMounted, `labels` from `fetchDocumentLabels`
 * in the route watcher — and nothing orders them. On a cold backend the label
 * map lands second, so a watch that re-hydrated unconditionally would reset the
 * draft to the server's (usually empty) map: the selection vanished, and since
 * Save is disabled on `!hasChanges`, the button went dead permanently with
 * nothing on screen explaining why. That is a silent data loss for the user,
 * and in CI the same race burned the full 180s e2e timeout about once per run
 * (#144).
 *
 * Cleared in exactly the two places the server becomes the truth again: after a
 * save round-trips, and when the parent swaps in a different document.
 */
const touched = ref(false)

// Re-hydrate the draft whenever the parent hands in a fresh label map — after a
// save round-trips through `saved`, or on a late-arriving initial fetch. Skipped
// once the user has touched the draft, so a slow fetch can never overwrite an
// edit in progress; `dirty` below still diffs against the newly-arrived
// `props.labels`, so a late map is accounted for without clobbering anything.
watch(
  () => props.labels,
  (next) => {
    if (touched.value) return
    draft.value = { ...next }
  },
)

// A different document is a different draft: drop any unsaved edit rather than
// carrying it across, which would otherwise offer to save one document's label
// onto another. `labels` for the new document may not have arrived yet, so
// clearing `touched` is what lets the watch above hydrate it when it does.
watch(
  () => props.documentId,
  () => {
    touched.value = false
    draft.value = { ...props.labels }
  },
)

/** Facet keys whose draft value differs from the last-saved label, mapped to
 * what the PUT should send: the new value key, or `null` to clear it. Only
 * these are sent — sending the whole draft would be wasteful, and omitting a
 * cleared key would silently leave the old label in place. */
const dirty = computed<Record<string, string | null>>(() => {
  const changes: Record<string, string | null> = {}
  for (const facet of props.facets) {
    const before = props.labels[facet.key] ?? ''
    const after = draft.value[facet.key] ?? ''
    if (before !== after) changes[facet.key] = after === '' ? null : after
  }
  return changes
})

const hasChanges = computed(() => Object.keys(dirty.value).length > 0)

function onSelect(facetKey: string, event: Event): void {
  const value = (event.target as HTMLSelectElement).value
  draft.value = { ...draft.value, [facetKey]: value }
  touched.value = true
}

async function save(): Promise<void> {
  if (saving.value || !hasChanges.value) return
  saving.value = true
  error.value = null
  try {
    const saved = await updateDocumentLabels(props.documentId, dirty.value)
    // Before the emit, not after: the parent assigns `labels` synchronously in
    // its handler, so leaving `touched` set here would make the watch above skip
    // the very re-hydration this save exists to produce.
    touched.value = false
    emit('saved', saved)
  } catch {
    error.value = 'Could not save these labels. Try again.'
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div id="document-facets-card" class="card p-5" data-testid="facet-editor">
    <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-3">Facets</h2>
    <div class="@container">
      <div class="flex flex-wrap items-end gap-3">
        <div v-for="facet in ordered" :key="facet.key">
          <label class="filter-label" :for="`facet-edit-${facet.key}`">{{ facet.label }}</label>
          <select
            :id="`facet-edit-${facet.key}`"
            class="form-select disabled:opacity-60"
            :data-testid="`facet-edit-${facet.key}`"
            :disabled="facet.values.length === 0"
            :value="draft[facet.key] ?? ''"
            @change="onSelect(facet.key, $event)"
          >
            <option value="">—</option>
            <option v-for="value in facet.values" :key="value.key" :value="value.key">
              {{ value.label }}
            </option>
          </select>
          <p v-if="facet.values.length === 0" class="mt-1 text-xs text-gray-400 dark:text-gray-500">
            No values yet
          </p>
        </div>

        <AppButton
          type="button"
          size="sm"
          :disabled="saving || !hasChanges"
          data-testid="facet-save"
          @click="save"
        >
          {{ saving ? 'Saving…' : 'Save labels' }}
        </AppButton>
      </div>
    </div>

    <p v-if="error" role="alert" class="mt-2 text-sm text-red-600 dark:text-red-400" data-testid="facet-error">
      {{ error }}
    </p>
  </div>
</template>
