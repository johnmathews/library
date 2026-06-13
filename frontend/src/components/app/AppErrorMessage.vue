<script setup lang="ts">
// Replaces GovErrorMessage. Preserves the optional `id` prop, the
// visually-hidden "Error:" prefix and the default slot for the message text.
const props = defineProps<{ id?: string }>()
</script>

<template>
  <p :id="props.id" class="text-sm text-red-500 mt-1">
    <span class="sr-only">Error:</span> <slot />
  </p>
</template>
