<script setup lang="ts">
/**
 * One `<select>` per facet that has values, AND-composing, for the document
 * list filter bar. Follows the mosaic field-row pattern (`.filter-label` +
 * `.form-select`, `flex flex-wrap items-end gap-3` — see
 * docs/frontend-view-principles.md §5).
 *
 * A facet renders only once it has **two or more** values. An empty select is
 * worse than an absent one, and a one-option select is barely better: it is a
 * filter you cannot use to compare anything, because every document it can
 * show carries the same value. The shipped vocabulary's `vehicle`, `property`
 * and `person` facets ship empty; in a real archive `property` is the one that
 * tends to sit at exactly one value for years, and it is the reason the
 * threshold is two rather than one. The rule is on the count, not on a named
 * key, so a facet that grows a second value comes back on its own.
 *
 * Values are ordered by label, not by the vocabulary's stored `ordinal`. The
 * ordinal is seed-insertion order — useful to the `/vocabulary` panel, which
 * exists to expose it, and meaningless to someone hunting one of `category`'s
 * nineteen entries in a dropdown. Sorting here rather than in
 * `load_vocabulary` deliberately leaves the server's canonical order (and so
 * the LLM labelling prompt) alone. FacetEditor.vue sorts the same way.
 */
import { computed } from 'vue'
import type { FacetRef } from '@/api/facets'

const props = defineProps<{
  facets: FacetRef[]
  modelValue: Record<string, string>
}>()

const emit = defineEmits<{ 'update:modelValue': [Record<string, string>] }>()

/** Facets worth offering, each with its values in label order. */
const usable = computed<FacetRef[]>(() =>
  props.facets
    .filter((facet) => facet.values.length > 1)
    .map((facet) => ({
      ...facet,
      values: [...facet.values].sort((a, b) => a.label.localeCompare(b.label)),
    })),
)
const hasSelection = computed<boolean>(() => Object.keys(props.modelValue).length > 0)

function onSelect(facetKey: string, event: Event): void {
  const value = (event.target as HTMLSelectElement).value
  const next = { ...props.modelValue }
  if (value) next[facetKey] = value
  else delete next[facetKey]
  emit('update:modelValue', next)
}

function clearAll(): void {
  emit('update:modelValue', {})
}
</script>

<template>
  <div class="@container">
    <div class="flex flex-wrap items-end gap-3" data-testid="facet-filter-bar">
      <div v-for="facet in usable" :key="facet.key">
        <label class="filter-label" :for="`facet-select-${facet.key}`">{{ facet.label }}</label>
        <select
          :id="`facet-select-${facet.key}`"
          class="form-select"
          data-facet-select="true"
          :data-testid="`facet-select-${facet.key}`"
          :value="modelValue[facet.key] ?? ''"
          :aria-label="facet.label"
          @change="onSelect(facet.key, $event)"
        >
          <option value="">Any</option>
          <option v-for="value in facet.values" :key="value.key" :value="value.key">
            {{ value.label }}
          </option>
        </select>
      </div>

      <button
        v-if="hasSelection"
        type="button"
        class="btn-sm text-violet-600 hover:text-violet-700 dark:text-violet-400"
        data-testid="facet-clear"
        @click="clearAll"
      >
        Clear facets
      </button>
    </div>
  </div>
</template>
