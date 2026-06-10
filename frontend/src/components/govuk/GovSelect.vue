<script setup lang="ts">
import { computed } from 'vue'
import type { SelectItem } from './types'

const props = defineProps<{
  id: string
  label: string
  items: SelectItem[]
  name?: string
  hint?: string
  errorMessage?: string
}>()

const model = defineModel<string>({ default: '' })

const describedBy = computed(() => {
  const ids: string[] = []
  if (props.hint) ids.push(`${props.id}-hint`)
  if (props.errorMessage) ids.push(`${props.id}-error`)
  return ids.length ? ids.join(' ') : undefined
})
</script>

<template>
  <div class="govuk-form-group" :class="{ 'govuk-form-group--error': props.errorMessage }">
    <label class="govuk-label" :for="props.id">{{ props.label }}</label>
    <div v-if="props.hint" :id="`${props.id}-hint`" class="govuk-hint">{{ props.hint }}</div>
    <p v-if="props.errorMessage" :id="`${props.id}-error`" class="govuk-error-message">
      <span class="govuk-visually-hidden">Error:</span> {{ props.errorMessage }}
    </p>
    <select
      v-model="model"
      class="govuk-select"
      :class="{ 'govuk-select--error': props.errorMessage }"
      :id="props.id"
      :name="props.name ?? props.id"
      :aria-describedby="describedBy"
    >
      <option v-for="item in props.items" :key="item.value" :value="item.value" :disabled="item.disabled">
        {{ item.text }}
      </option>
    </select>
  </div>
</template>
