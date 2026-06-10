<script setup lang="ts">
import { computed } from 'vue'
import type { ChoiceItem } from './types'

// Conditional reveal is driven reactively by Vue (class + aria attributes
// bound to the model) instead of initialising the govuk-frontend Radios JS,
// which would fight Vue for ownership of the same DOM.
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
  <div class="govuk-form-group" :class="{ 'govuk-form-group--error': props.errorMessage }">
    <fieldset class="govuk-fieldset" :aria-describedby="describedBy">
      <legend
        class="govuk-fieldset__legend"
        :class="{ 'govuk-fieldset__legend--l': props.legendIsPageHeading }"
      >
        <h1 v-if="props.legendIsPageHeading" class="govuk-fieldset__heading">{{ props.legend }}</h1>
        <template v-else>{{ props.legend }}</template>
      </legend>
      <div v-if="props.hint" :id="`${props.id}-hint`" class="govuk-hint">{{ props.hint }}</div>
      <p v-if="props.errorMessage" :id="`${props.id}-error`" class="govuk-error-message">
        <span class="govuk-visually-hidden">Error:</span> {{ props.errorMessage }}
      </p>
      <div
        class="govuk-radios"
        :class="{ 'govuk-radios--small': props.small, 'govuk-radios--inline': props.inline }"
        data-module="govuk-radios"
      >
        <template v-for="(item, index) in props.items" :key="item.value">
          <div class="govuk-radios__item">
            <input
              v-model="model"
              class="govuk-radios__input"
              :id="itemId(index)"
              :name="name"
              type="radio"
              :value="item.value"
              :data-aria-controls="item.conditional ? `conditional-${itemId(index)}` : undefined"
              :aria-expanded="item.conditional ? model === item.value : undefined"
              :aria-describedby="item.hint ? `${itemId(index)}-item-hint` : undefined"
            />
            <label class="govuk-label govuk-radios__label" :for="itemId(index)">{{ item.text }}</label>
            <div
              v-if="item.hint"
              :id="`${itemId(index)}-item-hint`"
              class="govuk-hint govuk-radios__hint"
            >
              {{ item.hint }}
            </div>
          </div>
          <div
            v-if="item.conditional"
            class="govuk-radios__conditional"
            :class="{ 'govuk-radios__conditional--hidden': model !== item.value }"
            :id="`conditional-${itemId(index)}`"
          >
            <slot :name="`conditional-${item.value}`" />
          </div>
        </template>
      </div>
    </fieldset>
  </div>
</template>
