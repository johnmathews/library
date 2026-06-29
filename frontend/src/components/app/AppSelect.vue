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
  /** Visually hide the label (kept for screen readers); see AppInput. */
  hideLabel?: boolean
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
  <div>
    <label
      :class="props.hideLabel ? 'sr-only' : 'block text-sm font-medium mb-1 text-gray-700 dark:text-gray-300'"
      :for="props.id"
    >{{ props.label }}</label>
    <p
      v-if="props.hint"
      :id="`${props.id}-hint`"
      class="text-sm text-gray-500 dark:text-gray-400 mb-1"
    >{{ props.hint }}</p>
    <select
      v-model="model"
      class="form-select w-full"
      :class="{ 'border-red-300': props.errorMessage }"
      :id="props.id"
      :name="props.name ?? props.id"
      :aria-describedby="describedBy"
      :aria-invalid="props.errorMessage ? 'true' : undefined"
    >
      <option
        v-for="item in props.items"
        :key="item.value"
        :value="item.value"
        :disabled="item.disabled"
      >
        {{ item.text }}
      </option>
    </select>
    <p
      v-if="props.errorMessage"
      :id="`${props.id}-error`"
      class="text-sm text-red-500 mt-1"
    >{{ props.errorMessage }}</p>
  </div>
</template>
