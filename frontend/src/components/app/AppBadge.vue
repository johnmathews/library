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
  // Design system: TagChip. A colour means one thing (brand spec §3.2): yellow
  // is "needs you", red is "failed". Everything else is the neutral chip, and
  // a finished state (green) adds a check icon instead of a colour (§8 call 4).
  grey: 'bg-tile text-ink',
  green: 'bg-tile text-ink',
  turquoise: 'bg-tile text-ink',
  blue: 'bg-tile text-ink',
  'light-blue': 'bg-tile text-ink',
  red: 'bg-deficit-tint text-error-ink',
  yellow: 'bg-attention-tint text-attention',
  orange: 'bg-attention-tint text-attention',
  purple: 'bg-tile text-ink',
  pink: 'bg-tile text-ink',
}

const DEFAULT_CLASSES = colourClasses.grey

const classes = computed(() => colourClasses[props.colour ?? 'grey'] ?? DEFAULT_CLASSES)

// A finished state keeps the neutral chip and says so with a check icon.
const showCheck = computed(() => props.colour === 'green' || props.colour === 'turquoise')
</script>

<template>
  <span
    class="inline-flex items-center gap-1 text-[12.5px] leading-4 font-semibold px-2.5 py-[3px] rounded-full"
    :class="classes"
  >
    <svg
      v-if="showCheck"
      class="h-3 w-3 shrink-0"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="3"
      stroke-linecap="round"
      stroke-linejoin="round"
      aria-hidden="true"
      data-testid="badge-check"
    >
      <path d="M5 12.5l4.5 4.5L19 7.5" />
    </svg>
    <slot />
  </span>
</template>
