<script setup lang="ts">
// GOV.UK has no progress bar component; this is an app extension styled
// with the design system's colours and type scale (see docs/frontend.md).
// Accessible: role="progressbar" with aria-valuenow announced by screen
// readers; the visible percentage doubles as the text alternative.
import { computed } from 'vue'

const props = defineProps<{
  /** Accessible name, e.g. "Uploading invoice.pdf". */
  label: string
  /** 0–100. */
  value: number
}>()

const clamped = computed(() => Math.max(0, Math.min(100, Math.round(props.value))))
</script>

<template>
  <div class="app-progress">
    <div
      class="app-progress__track"
      role="progressbar"
      :aria-label="props.label"
      :aria-valuenow="clamped"
      aria-valuemin="0"
      aria-valuemax="100"
    >
      <div class="app-progress__fill" :style="{ width: `${clamped}%` }"></div>
    </div>
    <span class="app-progress__count" aria-hidden="true">{{ clamped }}%</span>
  </div>
</template>
