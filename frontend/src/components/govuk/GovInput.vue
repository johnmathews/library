<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  id: string
  label: string
  name?: string
  type?: string
  hint?: string
  errorMessage?: string
  autocomplete?: string
  inputmode?: 'text' | 'numeric' | 'decimal' | 'email' | 'tel' | 'search' | 'url'
  spellcheck?: boolean
  widthClass?: string
  labelIsPageHeading?: boolean
  /** id of a `<datalist>` for native autocomplete suggestions. */
  list?: string
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
    <h1 v-if="props.labelIsPageHeading" class="govuk-label-wrapper">
      <label class="govuk-label govuk-label--l" :for="props.id">{{ props.label }}</label>
    </h1>
    <label v-else class="govuk-label" :for="props.id">{{ props.label }}</label>
    <div v-if="props.hint" :id="`${props.id}-hint`" class="govuk-hint">{{ props.hint }}</div>
    <p v-if="props.errorMessage" :id="`${props.id}-error`" class="govuk-error-message">
      <span class="govuk-visually-hidden">Error:</span> {{ props.errorMessage }}
    </p>
    <input
      v-model="model"
      class="govuk-input"
      :class="[props.widthClass, { 'govuk-input--error': props.errorMessage }]"
      :id="props.id"
      :name="props.name ?? props.id"
      :type="props.type ?? 'text'"
      :autocomplete="props.autocomplete"
      :inputmode="props.inputmode"
      :spellcheck="props.spellcheck"
      :list="props.list"
      :aria-describedby="describedBy"
    />
  </div>
</template>
