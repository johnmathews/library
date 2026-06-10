<script setup lang="ts">
const props = defineProps<{
  colour?: 'grey' | 'green' | 'turquoise' | 'blue' | 'light-blue' | 'purple' | 'pink' | 'red' | 'orange' | 'yellow'
}>()
</script>

<template>
  <strong class="govuk-tag" :class="props.colour ? `govuk-tag--${props.colour}` : undefined">
    <slot />
  </strong>
</template>
