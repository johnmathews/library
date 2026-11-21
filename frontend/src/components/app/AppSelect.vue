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
  /** `data-testid` for the inner `<select>` itself (not the wrapper), so tests
      and Playwright `.selectOption()`/`.setValue()` can target the control. A
      bare `data-testid` attribute would fall through to the wrapper `<div>`,
      and setting a value on a div fails. Mirrors AppInput's `testid`. */
  testid?: string
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
      :class="props.hideLabel ? 'sr-only' : 'block text-sm font-medium mb-1 text-ink'"
      :for="props.id"
    >{{ props.label }}</label>
    <p
      v-if="props.hint"
      :id="`${props.id}-hint`"
      class="text-sm text-ink-secondary mb-1"
    >{{ props.hint }}</p>
    <select
      v-model="model"
      class="form-select w-full"
      :class="{ 'border-error-ink': props.errorMessage }"
      :id="props.id"
      :name="props.name ?? props.id"
      :data-testid="props.testid"
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
      class="text-sm text-error-ink mt-1"
    >{{ props.errorMessage }}</p>
  </div>
</template>
