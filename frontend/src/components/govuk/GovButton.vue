<script setup lang="ts">
import { ref } from 'vue'
import { Button } from 'govuk-frontend'
import { useGovukComponent } from './useGovukComponent'

const props = withDefaults(
  defineProps<{
    variant?: 'primary' | 'secondary' | 'warning' | 'inverse'
    type?: 'submit' | 'button' | 'reset'
    href?: string
    disabled?: boolean
    preventDoubleClick?: boolean
  }>(),
  { variant: 'primary', type: 'submit', disabled: false, preventDoubleClick: false },
)

const root = ref<HTMLElement | null>(null)
useGovukComponent(root, Button, { preventDoubleClick: props.preventDoubleClick })
</script>

<template>
  <a
    v-if="props.href"
    ref="root"
    :href="props.href"
    role="button"
    draggable="false"
    class="govuk-button"
    :class="{ [`govuk-button--${props.variant}`]: props.variant !== 'primary' }"
    data-module="govuk-button"
  >
    <slot />
  </a>
  <button
    v-else
    ref="root"
    :type="props.type"
    class="govuk-button"
    :class="{ [`govuk-button--${props.variant}`]: props.variant !== 'primary' }"
    data-module="govuk-button"
    :disabled="props.disabled"
    :aria-disabled="props.disabled || undefined"
  >
    <slot />
  </button>
</template>
