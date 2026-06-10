<script setup lang="ts">
const props = defineProps<{ id?: string }>()
</script>

<template>
  <p :id="props.id" class="govuk-error-message">
    <span class="govuk-visually-hidden">Error:</span> <slot />
  </p>
</template>
