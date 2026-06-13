<script setup lang="ts">
import { RouterLink } from 'vue-router'

// Replaces GovBackLink. Preserves the `text` (default 'Back'), `to` and `href`
// props: a RouterLink when `to` is set, otherwise a plain `<a>`.
const props = withDefaults(defineProps<{ text?: string; to?: string; href?: string }>(), {
  text: 'Back',
})
</script>

<template>
  <RouterLink
    v-if="props.to"
    :to="props.to"
    class="inline-flex items-center text-sm text-violet-500 hover:text-violet-600"
  >
    <svg class="w-3 h-3 mr-1 fill-current" viewBox="0 0 12 12" aria-hidden="true">
      <path d="M7.4 1.4 6 0 0 6l6 6 1.4-1.4L2.8 6z" />
    </svg>
    {{ props.text }}
  </RouterLink>
  <a
    v-else
    :href="props.href ?? '#'"
    class="inline-flex items-center text-sm text-violet-500 hover:text-violet-600"
  >
    <svg class="w-3 h-3 mr-1 fill-current" viewBox="0 0 12 12" aria-hidden="true">
      <path d="M7.4 1.4 6 0 0 6l6 6 1.4-1.4L2.8 6z" />
    </svg>
    {{ props.text }}
  </a>
</template>
