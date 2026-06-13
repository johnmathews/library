<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import type { ErrorSummaryItem } from './types'

// Replaces GovErrorSummary. GovErrorSummary preserved the `title`
// (default 'There is a problem') and `errors: ErrorSummaryItem[]` props and
// delegated accessibility to the govuk-frontend ErrorSummary module, which:
//   1. focuses the summary on init,
//   2. re-focuses it when the error list changes while mounted, and
//   3. moves focus to the target field when an error link is activated.
// govuk-frontend is not present on this Mosaic component, so that focus logic
// is replicated verbatim in plain DOM below.
const props = withDefaults(
  defineProps<{
    title?: string
    errors: ErrorSummaryItem[]
  }>(),
  { title: 'There is a problem' },
)

const root = ref<HTMLElement | null>(null)

// (1) Focus the summary on mount so screen readers announce the alert.
onMounted(() => root.value?.focus())

// (2) Re-focus when the error list changes while the summary stays mounted
// (e.g. a second failed submit) so the alert is re-announced.
watch(
  () => props.errors,
  () => root.value?.focus(),
)

// (3) Move focus to the field referenced by an error link's href fragment.
// Mirrors govuk-frontend ErrorSummary.handleClick: resolve the fragment to an
// element by id, make it programmatically focusable if it is not already, and
// focus it.
function focusTarget(href: string): boolean {
  const id = href.split('#')[1]
  if (!id) return false
  const el = document.getElementById(id)
  if (!el) return false

  if (!el.hasAttribute('tabindex')) {
    el.setAttribute('tabindex', '-1')
  }
  el.scrollIntoView()
  el.focus()
  return true
}

function onLinkClick(event: MouseEvent, href?: string): void {
  if (!href) return
  if (focusTarget(href)) {
    event.preventDefault()
  }
}
</script>

<template>
  <div
    ref="root"
    role="alert"
    tabindex="-1"
    class="bg-red-50 dark:bg-red-500/10 border border-red-300 dark:border-red-500/30 rounded-lg p-4 mb-6"
  >
    <h2 class="text-red-800 dark:text-red-400 font-semibold mb-2">{{ props.title }}</h2>
    <ul>
      <li v-for="(error, index) in props.errors" :key="index">
        <a
          v-if="error.href"
          :href="error.href"
          class="text-red-700 dark:text-red-400 underline"
          @click="onLinkClick($event, error.href)"
          >{{ error.text }}</a
        >
        <template v-else>{{ error.text }}</template>
      </li>
    </ul>
  </div>
</template>
