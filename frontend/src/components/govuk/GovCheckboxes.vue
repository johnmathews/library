<script setup lang="ts">
import { computed } from 'vue'
import type { ChoiceItem } from './types'

// Conditional reveal handled reactively by Vue (see GovRadios.vue).
const props = defineProps<{
  id: string
  legend: string
  items: ChoiceItem[]
  name?: string
  hint?: string
  errorMessage?: string
  small?: boolean
  legendSize?: 'l' | 'm' | 's'
}>()

const model = defineModel<string[]>({ default: () => [] })

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

function isChecked(value: string): boolean {
  return model.value.includes(value)
}
</script>

<template>
  <div class="govuk-form-group" :class="{ 'govuk-form-group--error': props.errorMessage }">
    <fieldset class="govuk-fieldset" :aria-describedby="describedBy">
      <legend
        class="govuk-fieldset__legend"
        :class="props.legendSize ? `govuk-fieldset__legend--${props.legendSize}` : undefined"
      >{{ props.legend }}</legend>
      <div v-if="props.hint" :id="`${props.id}-hint`" class="govuk-hint">{{ props.hint }}</div>
      <p v-if="props.errorMessage" :id="`${props.id}-error`" class="govuk-error-message">
        <span class="govuk-visually-hidden">Error:</span> {{ props.errorMessage }}
      </p>
      <div
        class="govuk-checkboxes"
        :class="{ 'govuk-checkboxes--small': props.small }"
        data-module="govuk-checkboxes"
      >
        <template v-for="(item, index) in props.items" :key="item.value">
          <div class="govuk-checkboxes__item">
            <input
              v-model="model"
              class="govuk-checkboxes__input"
              :id="itemId(index)"
              :name="name"
              type="checkbox"
              :value="item.value"
              :data-aria-controls="item.conditional ? `conditional-${itemId(index)}` : undefined"
              :aria-expanded="item.conditional ? isChecked(item.value) : undefined"
              :aria-describedby="item.hint ? `${itemId(index)}-item-hint` : undefined"
            />
            <label class="govuk-label govuk-checkboxes__label" :for="itemId(index)">
              {{ item.text }}
            </label>
            <div
              v-if="item.hint"
              :id="`${itemId(index)}-item-hint`"
              class="govuk-hint govuk-checkboxes__hint"
            >
              {{ item.hint }}
            </div>
          </div>
          <div
            v-if="item.conditional"
            class="govuk-checkboxes__conditional"
            :class="{ 'govuk-checkboxes__conditional--hidden': !isChecked(item.value) }"
            :id="`conditional-${itemId(index)}`"
          >
            <slot :name="`conditional-${item.value}`" />
          </div>
        </template>
      </div>
    </fieldset>
  </div>
</template>
