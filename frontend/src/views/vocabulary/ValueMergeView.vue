<script setup lang="ts">
/**
 * Merge confirmation page (route `/vocabulary/:facetKey/:valueKey/merge`).
 *
 * The one irreversible operation in the vocabulary panel: merging folds a
 * source value's documents, key and aliases into a target value and deletes
 * the source outright (colour override included). Per the GOV.UK pattern
 * `router/index.ts` states on `document-delete`, a destructive action this
 * size gets a confirmation PAGE with its own URL — back-button friendly,
 * never a JS-only modal.
 *
 * The governing constraint (facets.md): every vocabulary edit shows a diff
 * approved before it is applied, and that preview must always belong to the
 * currently selected target. Changing the target invalidates the previous
 * dry run's count IMMEDIATELY, before the new one resolves — otherwise the
 * page could show a count computed for target A beside an Apply button that
 * merges into target B. `canApply` therefore checks not just "do we have a
 * count" but "is that count *for the value in the select right now*".
 */
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { fetchFacets, mergeValue, type FacetRef, type FacetValueRef } from '@/api/facets'
import { ApiError } from '@/api/client'
import { AppBackLink } from '@/components/app'

const route = useRoute()
const router = useRouter()

const facetKey = String(route.params.facetKey)
const valueKey = String(route.params.valueKey)

const vocabulary = ref<FacetRef[]>([])
const loading = ref(true)
const loadError = ref<string | null>(null)

const facet = computed<FacetRef | undefined>(() =>
  vocabulary.value.find((f) => f.key === facetKey),
)
const source = computed<FacetValueRef | undefined>(() =>
  facet.value?.values.find((v) => v.key === valueKey),
)
/** Every other value in the facet: the self-merge 409 is unreachable through
 * this list, though the route can still answer it (see `apply` below). */
const targetOptions = computed<FacetValueRef[]>(
  () => facet.value?.values.filter((v) => v.key !== valueKey) ?? [],
)
const targetValue = computed<FacetValueRef | undefined>(() =>
  targetOptions.value.find((v) => v.key === target.value),
)

async function load(): Promise<void> {
  loading.value = true
  loadError.value = null
  try {
    vocabulary.value = await fetchFacets()
  } catch {
    loadError.value = 'Could not load the facet vocabulary. Try refreshing the page.'
  } finally {
    loading.value = false
  }
}
void load()

// --- Dry-run preview -----------------------------------------------------

const target = ref('')
const moved = ref<number | null>(null)
/** The target `moved` belongs to — not necessarily `target.value` right now. */
const previewFor = ref<string | null>(null)
const previewing = ref(false)
const error = ref<string | null>(null)

watch(target, async (next) => {
  // Eager invalidation: the instant the target changes, the old count no
  // longer describes anything on screen and Apply must go off — not after
  // the new dry run resolves.
  moved.value = null
  previewFor.value = null
  error.value = null
  if (!next) return
  previewing.value = true
  try {
    const result = await mergeValue(facetKey, valueKey, next, true)
    // A later change already superseded this response; let that one win.
    if (target.value !== next) return
    moved.value = result.moved
    previewFor.value = next
  } catch (err) {
    // Same supersession guard as the success path above: a request for a
    // target the owner has already moved on from must not render its error
    // beside a later, valid preview for the target now selected.
    if (target.value !== next) return
    error.value = err instanceof ApiError ? err.detail : 'Could not preview the merge.'
  } finally {
    previewing.value = false
  }
})

const canApply = computed(() => previewFor.value !== null && previewFor.value === target.value)

// --- Diff parts computed from the already-loaded vocabulary --------------
// Only `moved` comes from the server; gained/shared aliases and the colour
// loss are derived locally from `source` and `targetValue`.

const gainedAliases = computed<string[]>(() => {
  const src = source.value
  const tgt = targetValue.value
  if (!src || !tgt) return []
  const have = new Set(tgt.aliases.map((a) => a.toLowerCase()))
  return src.aliases.filter((a) => !have.has(a.toLowerCase()))
})

const sharedAliases = computed<string[]>(() => {
  const src = source.value
  const tgt = targetValue.value
  if (!src || !tgt) return []
  const have = new Set(tgt.aliases.map((a) => a.toLowerCase()))
  return src.aliases.filter((a) => have.has(a.toLowerCase()))
})

const losesColour = computed(() => source.value?.colour != null)

// --- Apply -----------------------------------------------------------------

const applying = ref(false)

async function apply(): Promise<void> {
  if (!canApply.value || applying.value) return
  applying.value = true
  error.value = null
  try {
    await mergeValue(facetKey, valueKey, target.value, false)
    await router.push({ name: 'vocabulary' })
  } catch (err) {
    // Still handled even though the target select can never offer the
    // source itself — the route can answer this 409 regardless.
    error.value = err instanceof ApiError ? err.detail : 'Could not merge the value.'
    applying.value = false
  }
}
</script>

<template>
  <div id="value-merge-page">
    <div class="mb-4">
      <AppBackLink :to="'/vocabulary'" text="Back to vocabulary" />
    </div>

    <p v-if="loading" class="text-sm text-gray-500 dark:text-gray-400" data-testid="merge-loading">
      Loading the facet vocabulary…
    </p>

    <div
      v-else-if="loadError"
      role="alert"
      class="border-l-4 border-red-500 bg-red-50 dark:bg-red-500/10 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-300"
      data-testid="merge-load-error"
    >
      {{ loadError }}
    </div>

    <div
      v-else-if="!facet || !source"
      role="alert"
      class="border-l-4 border-red-500 bg-red-50 dark:bg-red-500/10 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-300"
      data-testid="merge-not-found"
    >
      That value could not be found. It may already have been merged or deleted.
    </div>

    <div v-else class="max-w-2xl">
      <h1 class="text-2xl md:text-3xl text-gray-800 dark:text-gray-100 font-bold mb-2">
        Merge “{{ source.label }}”
      </h1>
      <p class="text-gray-600 dark:text-gray-300 mb-6">
        Every document labelled “{{ source.label }}” in {{ facet.label }} will be relabelled to the
        value you choose below, and “{{ source.label }}” will be deleted. This cannot be undone.
      </p>

      <div class="mb-4">
        <label for="merge-target" class="filter-label">Merge into</label>
        <select
          id="merge-target"
          v-model="target"
          class="form-select"
          data-testid="merge-target"
        >
          <option value="" disabled>Choose a value</option>
          <option v-for="option in targetOptions" :key="option.key" :value="option.key">
            {{ option.label }}
          </option>
        </select>
      </div>

      <p v-if="previewing" class="text-sm text-gray-500 dark:text-gray-400" data-testid="merge-previewing">
        Checking how many documents would move…
      </p>

      <p
        v-if="error"
        role="alert"
        class="text-sm text-red-600 dark:text-red-400 mb-4"
        data-testid="merge-error"
      >
        {{ error }}
      </p>

      <div
        v-if="canApply && targetValue"
        class="border border-gray-200 dark:border-gray-700/60 rounded-lg p-4 mb-6"
        data-testid="merge-diff"
      >
        <h2 class="text-xs uppercase font-semibold text-gray-500 dark:text-gray-400 mb-2">
          What will change
        </h2>
        <ul class="space-y-1 text-sm">
          <li>
            <span class="text-amber-600 dark:text-amber-400">~</span>
            {{ moved }} documents relabelled — {{ source.label }} → {{ targetValue.label }}
          </li>
          <li>
            <span class="text-green-600 dark:text-green-400">+</span>
            gains alias "{{ source.key }}"
          </li>
          <li v-for="alias in gainedAliases" :key="`gain-${alias}`">
            <span class="text-green-600 dark:text-green-400">+</span>
            gains alias "{{ alias }}"
          </li>
          <li v-for="alias in sharedAliases" :key="`have-${alias}`">
            <span class="text-gray-400 dark:text-gray-500">=</span>
            already has alias "{{ alias }}"
          </li>
          <li>
            <span class="text-red-600 dark:text-red-400">−</span>
            {{ source.label }} is deleted
          </li>
          <li v-if="losesColour" data-testid="merge-colour-loss">
            <span class="text-red-600 dark:text-red-400">−</span>
            its colour override is lost
          </li>
        </ul>
      </div>

      <div class="flex flex-wrap items-center gap-3">
        <button
          type="button"
          class="btn bg-red-500 text-white hover:bg-red-600"
          :disabled="!canApply || applying"
          data-testid="merge-apply"
          @click="apply()"
        >
          {{ applying ? 'Merging…' : 'Yes, merge' }}
        </button>
        <AppBackLink :to="'/vocabulary'" text="Cancel" />
      </div>
    </div>
  </div>
</template>
