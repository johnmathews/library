<script setup lang="ts">
// Replaces GovPanel. Preserves GovPanel's `title` prop + default (body) slot.
const props = defineProps<{ title: string }>()
</script>

<template>
  <div class="bg-violet-500 text-white rounded-xl p-6 text-center">
    <h1 class="text-2xl font-bold">{{ props.title }}</h1>
    <div class="mt-2">
      <slot />
    </div>
  </div>
</template>
