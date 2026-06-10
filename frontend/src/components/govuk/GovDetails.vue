<script setup lang="ts">
const props = defineProps<{
  summary: string
  /** Render initially expanded (native `open` attribute). */
  open?: boolean
}>()
</script>

<template>
  <details class="govuk-details" :open="props.open">
    <summary class="govuk-details__summary">
      <span class="govuk-details__summary-text">{{ props.summary }}</span>
    </summary>
    <div class="govuk-details__text">
      <slot />
    </div>
  </details>
</template>
