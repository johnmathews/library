<script setup lang="ts">
/**
 * Facets tab: the full controlled facet vocabulary — every facet and its
 * values, with both a money-scoped chart count and a labelled-document count,
 * split colour (with same-colour collision marking), rename, alias, create-
 * value, create-facet and delete.
 *
 * State shape follows `TaxonomyCrudPanel.vue`'s idiom (per-row pending set,
 * per-row error record, at most one row in edit mode) but is NOT driven
 * through its `TaxonomyDescriptor` contract: that contract is flat, id-keyed,
 * treats a rename collision as a merge prompt, and deletes with reassignment.
 * None of those four things is true here — a value is composite-keyed
 * (facet:value), a rename is a label-only PATCH with no merge semantics, and
 * a delete is refused outright (409) rather than offered a reassignment.
 *
 * Loads lazily exactly as `AdminMetadataPanel` does: `watch(() => props.active
 * ...)` with a `loaded` flag, fetching on the first false -> true transition.
 */
import { computed, reactive, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import {
  addAlias,
  createFacet,
  createValue,
  deleteValue,
  fetchFacetCounts,
  fetchFacets,
  fetchLabelCounts,
  renameValue,
  setValueColour,
  type FacetRef,
  type FacetValueCount,
  type FacetValueRef,
  type LabelCount,
} from '@/api/facets'
import { ApiError } from '@/api/client'
import SplitColourPicker from '@/components/vocabulary/SplitColourPicker.vue'
import { resolveSplitColour } from '@/utils/splitPalette'
import { slugify } from '@/utils/slugify'

defineOptions({ name: 'FacetsPanel' })
const props = defineProps<{ active: boolean }>()

const rowKey = (facetKey: string, valueKey: string): string => `${facetKey}:${valueKey}`

// --- Load --------------------------------------------------------------

const vocabulary = ref<FacetRef[]>([])
const moneyCounts = ref<Map<string, FacetValueCount>>(new Map())
const labelCounts = ref<Map<string, LabelCount>>(new Map())
const loading = ref(false)
const loaded = ref(false)
const loadError = ref<string | null>(null)

const sortedFacets = computed(() =>
  [...vocabulary.value].sort((a, b) => a.ordinal - b.ordinal),
)

async function load(): Promise<void> {
  loading.value = true
  loadError.value = null
  try {
    const [facets, counts, labels] = await Promise.all([
      fetchFacets(),
      fetchFacetCounts(),
      fetchLabelCounts(),
    ])
    vocabulary.value = facets
    const mc = new Map<string, FacetValueCount>()
    for (const count of counts) mc.set(rowKey(count.facet_key, count.value_key), count)
    moneyCounts.value = mc
    const lc = new Map<string, LabelCount>()
    for (const label of labels) lc.set(rowKey(label.facet_key, label.value_key), label)
    labelCounts.value = lc
    loaded.value = true
  } catch {
    loadError.value = 'Could not load the facet vocabulary. Try refreshing the page.'
  } finally {
    loading.value = false
  }
}

// `immediate: true` because the panel's own test mounts with `active: true`
// already set (no false -> true transition to observe), matching
// AdminMetadataPanel's tab-open contract either way: load on the first
// moment `active` is true, whether that is at mount or on a later flip.
watch(
  () => props.active,
  (isActive) => {
    if (!isActive) return
    if (!loaded.value && !loading.value) void load()
  },
  { immediate: true },
)

function moneyCountFor(facetKey: string, valueKey: string): number {
  return moneyCounts.value.get(rowKey(facetKey, valueKey))?.documents ?? 0
}

function labelCountFor(facetKey: string, valueKey: string): number {
  return labelCounts.value.get(rowKey(facetKey, valueKey))?.labelled ?? 0
}

function findValue(facetKey: string, valueKey: string): FacetValueRef | undefined {
  return vocabulary.value.find((facet) => facet.key === facetKey)?.values.find(
    (value) => value.key === valueKey,
  )
}

// --- Theme (for colour resolution) --------------------------------------
// Reuses the mechanism `ThemeToggle.vue` toggles (a `dark` class on <html>)
// rather than inventing a second one. Read once at setup; the colour picker
// itself is the live control, so a stale read here only matters until the
// panel next reloads.
const isDark = ref(
  typeof document !== 'undefined' && document.documentElement.classList.contains('dark'),
)

// --- Same-colour collision marking --------------------------------------
// Per facet, bucket values by their RESOLVED colour (stored override or
// derived slot) and flag every value in a bucket with more than one member.
const collisionKeys = computed<Map<string, Set<string>>>(() => {
  const result = new Map<string, Set<string>>()
  for (const facet of vocabulary.value) {
    const buckets = new Map<string, string[]>()
    for (const value of facet.values) {
      const colour = resolveSplitColour(value.colour, value.key, isDark.value)
      const bucket = buckets.get(colour)
      if (bucket) bucket.push(value.key)
      else buckets.set(colour, [value.key])
    }
    const colliding = new Set<string>()
    for (const bucket of buckets.values()) {
      if (bucket.length > 1) {
        for (const key of bucket) colliding.add(key)
      }
    }
    result.set(facet.key, colliding)
  }
  return result
})

function hasCollision(facetKey: string, valueKey: string): boolean {
  return collisionKeys.value.get(facetKey)?.has(valueKey) ?? false
}

// --- Per-row state --------------------------------------------------------
// At most one row is in each of rename / alias / delete-confirm mode at once.

const pendingKeys = ref<Set<string>>(new Set())
const rowError = reactive<Record<string, string>>({})

function setPending(key: string, pending: boolean): void {
  const next = new Set(pendingKeys.value)
  if (pending) next.add(key)
  else next.delete(key)
  pendingKeys.value = next
}

function isPending(key: string): boolean {
  return pendingKeys.value.has(key)
}

function setRowError(key: string, message: string | null): void {
  if (message) rowError[key] = message
  else delete rowError[key]
}

/** After any successful mutation: reload the three GETs so counts, aliases
 * and colours stay truthful rather than being patched locally. */
async function afterMutation(): Promise<void> {
  await load()
}

const renameKey = ref<string | null>(null)
const renameText = ref('')

function startRename(facetKey: string, value: FacetValueRef): void {
  cancelAlias()
  cancelDelete()
  const key = rowKey(facetKey, value.key)
  renameKey.value = key
  renameText.value = value.label
  setRowError(key, null)
}

function cancelRename(): void {
  renameKey.value = null
  renameText.value = ''
}

async function saveRename(facetKey: string, valueKey: string): Promise<void> {
  const key = rowKey(facetKey, valueKey)
  const label = renameText.value.trim()
  if (!label) {
    setRowError(key, 'Enter a label.')
    return
  }
  setPending(key, true)
  setRowError(key, null)
  try {
    await renameValue(facetKey, valueKey, label)
    cancelRename()
    await afterMutation()
  } catch (err) {
    setRowError(key, err instanceof ApiError ? err.detail : 'Could not rename the value. Try again.')
  } finally {
    setPending(key, false)
  }
}

const aliasKey = ref<string | null>(null)
const aliasText = ref('')

function startAlias(facetKey: string, value: FacetValueRef): void {
  cancelRename()
  cancelDelete()
  const key = rowKey(facetKey, value.key)
  aliasKey.value = key
  aliasText.value = ''
  setRowError(key, null)
}

function cancelAlias(): void {
  aliasKey.value = null
  aliasText.value = ''
}

/** Adding an alias the value already has must not call the API: the route is
 * idempotent server-side (ON CONFLICT DO NOTHING) so it would answer 200 and
 * this panel would report a phantom addition. Check the loaded vocabulary
 * first.
 *
 * The comparison is deliberately CASE-INSENSITIVE, even though the server's
 * `facet_value_aliases` table stores aliases case-sensitively and its
 * `add_alias` does no lowering before its own `ON CONFLICT DO NOTHING`
 * (`src/library/facets/vocabulary.py`) — so a case-only variant is not a
 * server-side no-op on its own. It is still correctly blocked here because
 * the LABELLER resolves values and aliases casefolded (docs/facets.md §3;
 * `parse_label_response` in `src/library/facets/labeller.py`): a case-only
 * variant already resolves through the existing alias and would add nothing
 * but a dead, unreachable row. Do not "fix" this to exact equality — that
 * would let the owner create exactly that dead row.
 *
 * `casefold()`/`toLowerCase()` fold case but not diacritics, so an alias
 * differing only by a diacritic (e.g. `Skoda` vs. an existing `Škoda`) is a
 * genuinely distinct alias the labeller cannot resolve through the existing
 * one, and must still reach the API — see
 * `tests/test_facet_labeller.py::test_casefold_does_not_fold_diacritics` for
 * the backend side of the same boundary. */
async function saveAlias(facetKey: string, valueKey: string): Promise<void> {
  const key = rowKey(facetKey, valueKey)
  const alias = aliasText.value.trim()
  if (!alias) {
    setRowError(key, 'Enter an alias.')
    return
  }
  const value = findValue(facetKey, valueKey)
  const existingMatch = value?.aliases.find(
    (existing) => existing.trim().toLowerCase() === alias.toLowerCase(),
  )
  if (existingMatch) {
    setRowError(
      key,
      `Already covered by the alias '${existingMatch}' — aliases match case-insensitively.`,
    )
    return
  }
  setPending(key, true)
  setRowError(key, null)
  try {
    await addAlias(facetKey, valueKey, alias)
    cancelAlias()
    await afterMutation()
  } catch (err) {
    setRowError(key, err instanceof ApiError ? err.detail : 'Could not add the alias. Try again.')
  } finally {
    setPending(key, false)
  }
}

const deleteKey = ref<string | null>(null)

function startDelete(facetKey: string, valueKey: string): void {
  cancelRename()
  cancelAlias()
  const key = rowKey(facetKey, valueKey)
  deleteKey.value = key
  setRowError(key, null)
}

function cancelDelete(): void {
  deleteKey.value = null
}

/** A 409 renders the server's `detail` verbatim — it carries the only number
 * telling the owner what to do next (how many documents still carry it). */
async function confirmDelete(facetKey: string, valueKey: string): Promise<void> {
  const key = rowKey(facetKey, valueKey)
  setPending(key, true)
  setRowError(key, null)
  try {
    await deleteValue(facetKey, valueKey)
    deleteKey.value = null
    await afterMutation()
  } catch (err) {
    setRowError(key, err instanceof ApiError ? err.detail : 'Could not delete the value. Try again.')
  } finally {
    setPending(key, false)
  }
}

async function onColourChange(
  facetKey: string,
  valueKey: string,
  colour: string | null,
): Promise<void> {
  const key = rowKey(facetKey, valueKey)
  setPending(key, true)
  setRowError(key, null)
  try {
    await setValueColour(facetKey, valueKey, colour)
    await afterMutation()
  } catch (err) {
    setRowError(key, err instanceof ApiError ? err.detail : 'Could not set the colour. Try again.')
  } finally {
    setPending(key, false)
  }
}

// --- Create a facet ---------------------------------------------------
// Creating a facet is free and changes nothing on its own: `library
// label-archive` is CLI-only, so the success state says so rather than being
// silently untrue.

const newFacetKey = ref('')
const newFacetLabel = ref('')
const creatingFacet = ref(false)
const createFacetError = ref<string | null>(null)
const createFacetNote = ref<string | null>(null)

async function saveCreateFacet(): Promise<void> {
  const key = newFacetKey.value.trim()
  const label = newFacetLabel.value.trim()
  createFacetNote.value = null
  if (!key || !label) {
    createFacetError.value = 'Enter a key and a label.'
    return
  }
  creatingFacet.value = true
  createFacetError.value = null
  try {
    await createFacet(key, label)
    newFacetKey.value = ''
    newFacetLabel.value = ''
    createFacetNote.value =
      `'${label}' was created with no values. It carries no documents until the next ` +
      `'library label-archive' run.`
    await afterMutation()
  } catch (err) {
    createFacetError.value =
      err instanceof ApiError ? err.detail : 'Could not create the facet. Try again.'
  } finally {
    creatingFacet.value = false
  }
}

// --- Create a value (one form per facet) --------------------------------
// The key field is prefilled from the label via `slugify` (mirroring the
// server's `derive_value_key` for convenience only — the server remains the
// judge, and its 422 is rendered verbatim) but stays editable: once the
// owner edits the key directly, further label edits stop overwriting it.

interface CreateValueForm {
  open: boolean
  label: string
  key: string
  keyTouched: boolean
  creating: boolean
  error: string | null
}

const createValueForms = reactive<Record<string, CreateValueForm>>({})

function getCreateValueForm(facetKey: string): CreateValueForm {
  let form = createValueForms[facetKey]
  if (!form) {
    form = { open: false, label: '', key: '', keyTouched: false, creating: false, error: null }
    createValueForms[facetKey] = form
  }
  return form
}

function openCreateValue(facetKey: string): void {
  const form = getCreateValueForm(facetKey)
  form.open = true
  form.label = ''
  form.key = ''
  form.keyTouched = false
  form.error = null
}

function cancelCreateValue(facetKey: string): void {
  getCreateValueForm(facetKey).open = false
}

function onCreateValueLabelInput(facetKey: string, value: string): void {
  const form = getCreateValueForm(facetKey)
  form.label = value
  if (!form.keyTouched) form.key = slugify(value)
}

function onCreateValueKeyInput(facetKey: string, value: string): void {
  const form = getCreateValueForm(facetKey)
  form.key = value
  form.keyTouched = true
}

async function saveCreateValue(facetKey: string): Promise<void> {
  const form = getCreateValueForm(facetKey)
  const key = form.key.trim()
  const label = form.label.trim()
  if (!key || !label) {
    form.error = 'Enter a label and a key.'
    return
  }
  form.creating = true
  form.error = null
  try {
    await createValue(facetKey, key, label)
    form.open = false
    form.label = ''
    form.key = ''
    form.keyTouched = false
    await afterMutation()
  } catch (err) {
    form.error = err instanceof ApiError ? err.detail : 'Could not create the value. Try again.'
  } finally {
    form.creating = false
  }
}
</script>

<template>
  <div class="space-y-6">
    <!-- Create a facet -->
    <div class="card p-6">
      <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-4">Create a facet</h2>
      <div class="flex flex-wrap items-end gap-3">
        <div>
          <label for="create-facet-key" class="filter-label">Key</label>
          <input
            id="create-facet-key"
            v-model="newFacetKey"
            type="text"
            autocomplete="off"
            class="form-input"
            data-testid="create-facet-key"
          />
        </div>
        <div>
          <label for="create-facet-label" class="filter-label">Label</label>
          <input
            id="create-facet-label"
            v-model="newFacetLabel"
            type="text"
            autocomplete="off"
            class="form-input"
            data-testid="create-facet-label"
          />
        </div>
        <button
          type="button"
          class="btn"
          :disabled="creatingFacet"
          data-testid="create-facet-save"
          @click="saveCreateFacet()"
        >
          {{ creatingFacet ? 'Creating…' : 'Create facet' }}
        </button>
      </div>
      <p
        v-if="createFacetError"
        role="alert"
        class="mt-2 text-sm text-red-600 dark:text-red-400"
        data-testid="create-facet-error"
      >
        {{ createFacetError }}
      </p>
      <p
        v-if="createFacetNote"
        class="mt-2 text-sm text-gray-600 dark:text-gray-300"
        data-testid="create-facet-note"
      >
        {{ createFacetNote }}
      </p>
    </div>

    <!-- Loading / error -->
    <p v-if="loading" class="text-sm text-gray-500 dark:text-gray-400" data-testid="facets-loading">
      Loading the facet vocabulary…
    </p>
    <div
      v-else-if="loadError"
      role="alert"
      class="border-l-4 border-red-500 bg-red-50 dark:bg-red-500/10 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-300"
      data-testid="facets-error"
    >
      {{ loadError }}
    </div>

    <!-- Facets -->
    <div v-else v-for="facet in sortedFacets" :key="facet.key" class="card p-6 @container">
      <div class="mb-4 flex items-baseline gap-2">
        <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100">{{ facet.label }}</h2>
        <code class="text-xs text-gray-400 dark:text-gray-500">{{ facet.key }}</code>
      </div>

      <p
        v-if="facet.values.length === 0"
        class="text-sm text-gray-500 dark:text-gray-400"
        :data-testid="`facet-${facet.key}-empty`"
      >
        No values yet.
      </p>

      <ul v-else class="divide-y divide-gray-100 dark:divide-gray-700/60">
        <li
          v-for="value in facet.values"
          :key="value.key"
          class="py-3"
          :data-testid="`value-${facet.key}-${value.key}`"
        >
          <div class="flex flex-col gap-2 @md:flex-row @md:items-center @md:justify-between">
            <!-- Swatch + label/key/aliases -->
            <div class="flex min-w-0 items-center gap-2">
              <span
                class="h-4 w-4 shrink-0 rounded-full border border-gray-300 dark:border-gray-600"
                :style="{ backgroundColor: resolveSplitColour(value.colour, value.key, isDark) }"
                :aria-label="`Colour for ${value.label}`"
              />
              <div class="min-w-0">
                <div class="flex flex-wrap items-center gap-1.5">
                  <span class="font-medium text-gray-800 dark:text-gray-100">{{ value.label }}</span>
                  <code class="text-xs text-gray-400 dark:text-gray-500">{{ value.key }}</code>
                  <span
                    v-if="hasCollision(facet.key, value.key)"
                    class="rounded bg-yellow-100 px-1.5 py-0.5 text-xs font-semibold text-yellow-800 dark:bg-yellow-500/20 dark:text-yellow-300"
                    :data-testid="`value-${facet.key}-${value.key}-collision`"
                  >
                    Same colour as another value
                  </span>
                </div>
                <p v-if="value.aliases.length > 0" class="text-xs text-gray-500 dark:text-gray-400">
                  aka {{ value.aliases.join(', ') }}
                </p>
              </div>
            </div>

            <!-- Counts -->
            <div
              class="shrink-0 text-sm text-gray-600 dark:text-gray-300"
              :data-testid="`value-${facet.key}-${value.key}-counts`"
            >
              {{ labelCountFor(facet.key, value.key) }} labelled
              · {{ moneyCountFor(facet.key, value.key) }} in charts
            </div>

            <!-- Actions -->
            <div class="flex shrink-0 flex-wrap items-center gap-2">
              <button
                type="button"
                class="btn-xs border border-gray-200 dark:border-gray-700/60 text-gray-700 dark:text-gray-300"
                :disabled="isPending(rowKey(facet.key, value.key))"
                :data-testid="`value-${facet.key}-${value.key}-rename-btn`"
                @click="startRename(facet.key, value)"
              >
                Rename
              </button>
              <button
                type="button"
                class="btn-xs border border-gray-200 dark:border-gray-700/60 text-gray-700 dark:text-gray-300"
                :disabled="isPending(rowKey(facet.key, value.key))"
                :data-testid="`value-${facet.key}-${value.key}-alias-btn`"
                @click="startAlias(facet.key, value)"
              >
                Add alias
              </button>
              <RouterLink
                :to="{ name: 'vocabulary-merge', params: { facetKey: facet.key, valueKey: value.key } }"
                class="btn-xs border border-gray-200 dark:border-gray-700/60 text-gray-700 dark:text-gray-300"
                :data-testid="`value-${facet.key}-${value.key}-merge-btn`"
              >
                Merge
              </RouterLink>
              <button
                type="button"
                class="btn-xs border border-red-200 dark:border-red-500/40 text-red-600 dark:text-red-400"
                :disabled="isPending(rowKey(facet.key, value.key))"
                :data-testid="`value-${facet.key}-${value.key}-delete-btn`"
                @click="startDelete(facet.key, value.key)"
              >
                Delete
              </button>
            </div>
          </div>

          <!-- Colour picker -->
          <div class="mt-2">
            <SplitColourPicker
              :model-value="value.colour"
              :slot-key="value.key"
              :testid="`value-${facet.key}-${value.key}-colour`"
              @update:model-value="(colour) => onColourChange(facet.key, value.key, colour)"
            />
          </div>

          <!-- Rename editor -->
          <div
            v-if="renameKey === rowKey(facet.key, value.key)"
            class="mt-2 flex flex-wrap items-end gap-2"
          >
            <div>
              <label class="filter-label" :for="`rename-${facet.key}-${value.key}`">
                New label
              </label>
              <input
                :id="`rename-${facet.key}-${value.key}`"
                v-model="renameText"
                type="text"
                autocomplete="off"
                class="form-input"
                :data-testid="`value-${facet.key}-${value.key}-rename-input`"
                @keyup.enter="saveRename(facet.key, value.key)"
              />
            </div>
            <button
              type="button"
              class="btn-xs bg-violet-500 text-white hover:bg-violet-600"
              :disabled="isPending(rowKey(facet.key, value.key))"
              :data-testid="`value-${facet.key}-${value.key}-rename-save`"
              @click="saveRename(facet.key, value.key)"
            >
              Save
            </button>
            <button
              type="button"
              class="btn-xs border border-gray-200 dark:border-gray-700/60 text-gray-700 dark:text-gray-300"
              :data-testid="`value-${facet.key}-${value.key}-rename-cancel`"
              @click="cancelRename()"
            >
              Cancel
            </button>
          </div>

          <!-- Alias editor -->
          <div
            v-if="aliasKey === rowKey(facet.key, value.key)"
            class="mt-2 flex flex-wrap items-end gap-2"
          >
            <div>
              <label class="filter-label" :for="`alias-${facet.key}-${value.key}`">
                New alias
              </label>
              <input
                :id="`alias-${facet.key}-${value.key}`"
                v-model="aliasText"
                type="text"
                autocomplete="off"
                class="form-input"
                :data-testid="`value-${facet.key}-${value.key}-alias-input`"
                @keyup.enter="saveAlias(facet.key, value.key)"
              />
            </div>
            <button
              type="button"
              class="btn-xs bg-violet-500 text-white hover:bg-violet-600"
              :disabled="isPending(rowKey(facet.key, value.key))"
              :data-testid="`value-${facet.key}-${value.key}-alias-save`"
              @click="saveAlias(facet.key, value.key)"
            >
              Save
            </button>
            <button
              type="button"
              class="btn-xs border border-gray-200 dark:border-gray-700/60 text-gray-700 dark:text-gray-300"
              :data-testid="`value-${facet.key}-${value.key}-alias-cancel`"
              @click="cancelAlias()"
            >
              Cancel
            </button>
          </div>

          <!-- Delete confirm -->
          <div
            v-if="deleteKey === rowKey(facet.key, value.key)"
            class="mt-2 border-l-4 border-red-500 bg-red-50 dark:bg-red-500/10 rounded-lg px-3 py-2 text-sm text-gray-700 dark:text-gray-200"
          >
            <p class="mb-2">Delete '{{ value.label }}'? This cannot be undone.</p>
            <div class="flex gap-2">
              <button
                type="button"
                class="btn-xs bg-red-500 text-white hover:bg-red-600"
                :disabled="isPending(rowKey(facet.key, value.key))"
                :data-testid="`value-${facet.key}-${value.key}-delete-confirm`"
                @click="confirmDelete(facet.key, value.key)"
              >
                Delete
              </button>
              <button
                type="button"
                class="btn-xs border border-gray-200 dark:border-gray-700/60 text-gray-700 dark:text-gray-300"
                :data-testid="`value-${facet.key}-${value.key}-delete-cancel`"
                @click="cancelDelete()"
              >
                Cancel
              </button>
            </div>
          </div>

          <p
            v-if="rowError[rowKey(facet.key, value.key)]"
            role="alert"
            class="mt-1 text-xs text-red-600 dark:text-red-400"
            :data-testid="`value-${facet.key}-${value.key}-error`"
          >
            {{ rowError[rowKey(facet.key, value.key)] }}
          </p>
        </li>
      </ul>

      <!-- Add a value -->
      <div class="mt-4 border-t border-gray-100 pt-4 dark:border-gray-700/60">
        <button
          v-if="!getCreateValueForm(facet.key).open"
          type="button"
          class="btn-xs border border-gray-200 dark:border-gray-700/60 text-gray-700 dark:text-gray-300"
          :data-testid="`create-value-${facet.key}-btn`"
          @click="openCreateValue(facet.key)"
        >
          Add a value
        </button>
        <div v-else class="flex flex-wrap items-end gap-3">
          <div>
            <label class="filter-label" :for="`create-value-${facet.key}-label`">Label</label>
            <input
              :id="`create-value-${facet.key}-label`"
              type="text"
              autocomplete="off"
              class="form-input"
              :value="getCreateValueForm(facet.key).label"
              :data-testid="`create-value-${facet.key}-label`"
              @input="onCreateValueLabelInput(facet.key, ($event.target as HTMLInputElement).value)"
            />
          </div>
          <div>
            <label class="filter-label" :for="`create-value-${facet.key}-key`">Key</label>
            <input
              :id="`create-value-${facet.key}-key`"
              type="text"
              autocomplete="off"
              class="form-input"
              :value="getCreateValueForm(facet.key).key"
              :data-testid="`create-value-${facet.key}-key`"
              @input="onCreateValueKeyInput(facet.key, ($event.target as HTMLInputElement).value)"
            />
          </div>
          <button
            type="button"
            class="btn-xs bg-violet-500 text-white hover:bg-violet-600"
            :disabled="getCreateValueForm(facet.key).creating"
            :data-testid="`create-value-${facet.key}-save`"
            @click="saveCreateValue(facet.key)"
          >
            {{ getCreateValueForm(facet.key).creating ? 'Creating…' : 'Add value' }}
          </button>
          <button
            type="button"
            class="btn-xs border border-gray-200 dark:border-gray-700/60 text-gray-700 dark:text-gray-300"
            :data-testid="`create-value-${facet.key}-cancel`"
            @click="cancelCreateValue(facet.key)"
          >
            Cancel
          </button>
        </div>
        <p
          v-if="getCreateValueForm(facet.key).error"
          role="alert"
          class="mt-2 text-sm text-red-600 dark:text-red-400"
          :data-testid="`create-value-${facet.key}-error`"
        >
          {{ getCreateValueForm(facet.key).error }}
        </p>
      </div>
    </div>
  </div>
</template>
