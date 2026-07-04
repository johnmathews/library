<script setup lang="ts">
import { computed, onMounted, ref, useId } from 'vue'

// Replaces GovNotificationBanner. GovNotificationBanner exposed `variant?:
// 'success'` and an optional `title`, deriving its heading text from the
// variant ('Success' / 'Important') and using the govuk-frontend module to
// focus the banner on mount so it is announced. Both props and the
// focus-on-mount behaviour are preserved here. The `error` variant (red border,
// role="alert") was added so page-level load failures share this one banner
// surface (see docs/frontend.md §1.4) instead of hand-rolling the block.
const props = defineProps<{
  variant?: 'success' | 'error'
  title?: string
}>()

const success = computed(() => props.variant === 'success')
const error = computed(() => props.variant === 'error')
const titleText = computed(() => props.title ?? (success.value ? 'Success' : 'Important'))

const variantClass = computed(() =>
  success.value ? 'border-green-500' : error.value ? 'border-red-500' : 'border-sky-500',
)

// Both success and error announce themselves (role="alert"); the passive
// "Important" info banner does not. Only success steals focus on mount — an
// error surface announces via role="alert" without yanking focus away from
// whatever the user is doing (matches the hand-rolled load-error blocks it
// replaced).
const alert = computed(() => success.value || error.value)

const root = ref<HTMLElement | null>(null)

// Unique per instance so multiple banners on one page (e.g. AdminView's per-tab
// load-error surfaces) don't collide on a shared heading id.
const titleId = useId()

onMounted(() => { if (success.value) root.value?.focus() })
</script>

<template>
  <div
    ref="root"
    :role="alert ? 'alert' : undefined"
    :tabindex="success ? -1 : undefined"
    class="bg-white dark:bg-gray-800 border-l-4 rounded-lg px-4 py-3 shadow-xs"
    :class="variantClass"
    :aria-labelledby="titleId"
  >
    <h2 :id="titleId" class="font-semibold mb-1">{{ titleText }}</h2>
    <div>
      <slot />
    </div>
  </div>
</template>
