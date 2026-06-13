<script setup lang="ts">
import { computed } from 'vue'
import type { ChoiceItem } from '../govuk/types'

// Conditional reveal is driven reactively by Vue (v-if keyed by the model),
// matching GovRadios' Vue-owned reveal behaviour.
const props = defineProps<{
  id: string
  legend: string
  items: ChoiceItem[]
  name?: string
  hint?: string
  errorMessage?: string
  legendIsPageHeading?: boolean
  small?: boolean
  inline?: boolean
}>()

const model = defineModel<string>({ default: '' })

const name = computed(() => props.name ?? props.id)

const describedBy = computed(() => {
  const ids: string[] = []
  if (props.hint) ids.push(`${props.id}-hint`)
  if (props.errorMessage) ids.push(`${props.id}-error`)
  return ids.length ? ids.join(' ') : undefined
})

function itemId(index: number): string {
  return index === 0 ? props.id : `${props.id}-${index + 1}`
}
</script>

<template>
  <fieldset :aria-describedby="describedBy">
    <legend class="text-sm font-semibold mb-2">{{ props.legend }}</legend>
    <p
      v-if="props.hint"
      :id="`${props.id}-hint`"
      class="text-sm text-gray-500 dark:text-gray-400 mb-1"
    >{{ props.hint }}</p>
    <p
      v-if="props.errorMessage"
      :id="`${props.id}-error`"
      class="text-sm text-red-500 mt-1 mb-1"
    >{{ props.errorMessage }}</p>
    <div :class="{ 'flex gap-4': props.inline }">
      <template v-for="(item, index) in props.items" :key="item.value">
        <label class="flex items-center gap-2 py-1">
          <input
            v-model="model"
            class="form-radio"
            :id="itemId(index)"
            :name="name"
            type="radio"
            :value="item.value"
            :aria-expanded="item.conditional ? model === item.value : undefined"
            :aria-describedby="item.hint ? `${itemId(index)}-item-hint` : undefined"
          />
          <span>{{ item.text }}</span>
        </label>
        <p
          v-if="item.hint"
          :id="`${itemId(index)}-item-hint`"
          class="text-sm text-gray-500 dark:text-gray-400"
        >{{ item.hint }}</p>
        <div
          v-if="item.conditional && model === item.value"
          :id="`conditional-${itemId(index)}`"
          class="pl-6"
        >
          <slot :name="`conditional-${item.value}`" />
        </div>
      </template>
    </div>
  </fieldset>
</template>
