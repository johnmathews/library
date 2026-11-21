<script setup lang="ts">
// Replaces GovPanel. Preserves GovPanel's `title` prop + default (body) slot.
const props = defineProps<{ title: string }>()
</script>

<template>
  <div class="bg-brand-fill text-on-brand rounded-md p-6 text-center">
    <h1 class="text-2xl font-bold">{{ props.title }}</h1>
    <div class="mt-2">
      <slot />
    </div>
  </div>
</template>
