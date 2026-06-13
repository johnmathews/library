<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

// Replaces GovNotificationBanner. GovNotificationBanner exposed `variant?:
// 'success'` and an optional `title`, deriving its heading text from the
// variant ('Success' / 'Important') and using the govuk-frontend module to
// focus the banner on mount so it is announced. Both props and the
// focus-on-mount behaviour are preserved here.
const props = defineProps<{
  variant?: 'success'
  title?: string
}>()

const success = computed(() => props.variant === 'success')
const titleText = computed(() => props.title ?? (success.value ? 'Success' : 'Important'))

const variantClass = computed(() => (success.value ? 'border-green-500' : 'border-sky-500'))

const root = ref<HTMLElement | null>(null)

// Replicates GovNotificationBanner's announce-on-appear behaviour: only the
// success variant carries role="alert" and focuses itself on mount so it is
// announced. The non-success "Important" banner is passive and must not steal
// focus.
onMounted(() => { if (success.value) root.value?.focus() })
</script>

<template>
  <div
    ref="root"
    :role="success ? 'alert' : undefined"
    :tabindex="success ? -1 : undefined"
    class="bg-white dark:bg-gray-800 border-l-4 rounded-lg px-4 py-3 shadow-xs"
    :class="variantClass"
    aria-labelledby="app-banner-title"
  >
    <h2 id="app-banner-title" class="font-semibold mb-1">{{ titleText }}</h2>
    <div>
      <slot />
    </div>
  </div>
</template>
