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
  <div>
    <label
      class="block text-sm font-medium mb-1 text-gray-700 dark:text-gray-300"
      :for="props.id"
    >{{ props.label }}</label>
    <p
      v-if="props.hint"
      :id="`${props.id}-hint`"
      class="text-sm text-gray-500 dark:text-gray-400 mb-1"
    >{{ props.hint }}</p>
    <textarea
      v-model="model"
      class="form-textarea w-full"
      :class="{ 'border-red-300': props.errorMessage }"
      :id="props.id"
      :name="props.name ?? props.id"
      :rows="props.rows"
      :autocomplete="props.autocomplete"
      :aria-describedby="describedBy"
      :aria-invalid="props.errorMessage ? 'true' : undefined"
    ></textarea>
    <p
      v-if="props.errorMessage"
      :id="`${props.id}-error`"
      class="text-sm text-red-500 mt-1"
    >{{ props.errorMessage }}</p>
  </div>
</template>
