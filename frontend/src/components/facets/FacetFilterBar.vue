<script setup lang="ts">
/**
 * One `<select>` per facet that has values, AND-composing, for the document
 * list filter bar. Follows the mosaic field-row pattern (`.filter-label` +
 * `.form-select`, `flex flex-wrap items-end gap-3` — see
 * docs/frontend-view-principles.md §5, reference implementation
 * `components/charts/ChartControls.vue`).
 *
 * Facets with no values render nothing: the shipped vocabulary's `vehicle`,
 * `property` and `person` facets ship empty, and an empty select is worse
 * than an absent one.
 */
import { computed } from 'vue'
import type { FacetRef } from '@/api/facets'

const props = defineProps<{
  facets: FacetRef[]
  modelValue: Record<string, string>
}>()

const emit = defineEmits<{ 'update:modelValue': [Record<string, string>] }>()

const usable = computed<FacetRef[]>(() => props.facets.filter((facet) => facet.values.length > 0))
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
