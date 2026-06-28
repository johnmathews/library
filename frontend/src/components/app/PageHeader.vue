<script setup lang="ts">
/**
 * Shared page header: title + optional one-line description on the left, an
 * optional right-aligned `actions` slot (primary/secondary buttons) on the right.
 *
 * This is the canonical top-of-view primitive — see
 * docs/frontend-view-principles.md §1. It exists so views stop hand-rolling
 * `<h1>` + `<p>` + ad-hoc button placement (which led to inconsistent width and
 * Save buttons buried at the bottom of forms). It is full width and never
 * imposes a `max-w-*`; the shell (DefaultLayout) owns max width.
 *
 * Layout: on >= sm the title block and the actions sit on opposite ends of a
 * flex row; on small screens the actions wrap below the title.
 */
withDefaults(
  defineProps<{
    title: string
    description?: string
  }>(),
  { description: undefined },
)
</script>

<template>
  <div
    data-testid="page-header"
    class="w-full flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between mb-6"
  >
    <div class="min-w-0">
      <h1 class="text-2xl md:text-3xl text-gray-800 dark:text-gray-100 font-bold">
        {{ title }}
      </h1>
      <p v-if="description" class="text-gray-500 dark:text-gray-400 mt-1">
        {{ description }}
      </p>
      <slot name="description" />
    </div>
    <div v-if="$slots.actions" class="flex flex-wrap items-center gap-2 sm:flex-shrink-0">
      <slot name="actions" />
    </div>
  </div>
</template>
