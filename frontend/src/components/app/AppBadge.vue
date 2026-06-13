<script setup lang="ts">
import { computed } from 'vue'

// Replaces GovTag. GovTag exposed a single optional `colour` prop drawn from
// the GOV.UK tag palette plus a default slot; both are preserved here.
const props = defineProps<{
  colour?:
    | 'grey'
    | 'green'
    | 'turquoise'
    | 'blue'
    | 'light-blue'
    | 'purple'
    | 'pink'
    | 'red'
    | 'orange'
    | 'yellow'
}>()

// Map each GOV.UK tag colour onto a Mosaic {bg,text} pair. Unmapped/absent
// colours fall back to the grey/default pair.
const colourClasses: Record<string, string> = {
  grey: 'bg-gray-100 text-gray-600 dark:bg-gray-700/30 dark:text-gray-400',
  green: 'bg-green-100 text-green-700 dark:bg-green-400/30 dark:text-green-400',
  turquoise: 'bg-green-100 text-green-700 dark:bg-green-400/30 dark:text-green-400',
  blue: 'bg-sky-100 text-sky-700 dark:bg-sky-400/30 dark:text-sky-400',
  'light-blue': 'bg-sky-100 text-sky-700 dark:bg-sky-400/30 dark:text-sky-400',
  red: 'bg-red-100 text-red-700 dark:bg-red-400/30 dark:text-red-400',
  yellow: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-400/30 dark:text-yellow-400',
  orange: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-400/30 dark:text-yellow-400',
  purple: 'bg-violet-100 text-violet-700 dark:bg-violet-400/30 dark:text-violet-400',
  pink: 'bg-violet-100 text-violet-700 dark:bg-violet-400/30 dark:text-violet-400',
}

const DEFAULT_CLASSES = colourClasses.grey

const classes = computed(() => colourClasses[props.colour ?? 'grey'] ?? DEFAULT_CLASSES)
</script>

<template>
  <span
    class="inline-flex items-center text-xs font-medium px-2 py-0.5 rounded-full"
    :class="classes"
  >
    <slot />
  </span>
</template>
