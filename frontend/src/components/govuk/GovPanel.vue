<script setup lang="ts">
const props = defineProps<{ title: string }>()
</script>

<template>
  <div class="govuk-panel govuk-panel--confirmation">
    <h1 class="govuk-panel__title">{{ props.title }}</h1>
    <div class="govuk-panel__body">
      <slot />
    </div>
  </div>
</template>
