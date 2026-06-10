<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    id: string
    label: string
    name?: string
    rows?: number
    hint?: string
    errorMessage?: string
    autocomplete?: string
  }>(),
  { rows: 5 },
)

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
    <textarea
      v-model="model"
      class="govuk-textarea"
      :class="{ 'govuk-textarea--error': props.errorMessage }"
      :id="props.id"
      :name="props.name ?? props.id"
      :rows="props.rows"
      :autocomplete="props.autocomplete"
      :aria-describedby="describedBy"
    ></textarea>
  </div>
</template>
