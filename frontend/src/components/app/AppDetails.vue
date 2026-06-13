<script setup lang="ts">
// Replaces GovDetails. Preserves the `summary` and `open` props + default slot.
const props = defineProps<{
  summary: string
  /** Render initially expanded (native `open` attribute). */
  open?: boolean
}>()
</script>

<template>
  <details :open="props.open">
    <summary class="text-sm font-medium text-violet-500 cursor-pointer">{{ props.summary }}</summary>
    <div class="text-sm mt-2">
      <slot />
    </div>
  </details>
</template>
