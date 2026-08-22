<script setup lang="ts">
/**
 * Shared page header: the page's **lede** (an optional one-line description)
 * on the left and an optional right-aligned `actions` slot on the right.
 *
 * This is the canonical top-of-view primitive — see
 * docs/frontend-view-principles.md §1. It exists so views stop hand-rolling
 * `<h1>` + `<p>` + ad-hoc button placement (which led to inconsistent width and
 * Save buttons buried at the bottom of forms). It is full width and never
 * imposes a `max-w-*` on itself; the shell (DefaultLayout) owns max width.
 *
 * The `title` prop is still the view's to declare, but it is no longer rendered
 * here: it is claimed for the **app bar** via `usePageTitle` (see that file for
 * why). The `<h1>` therefore still exists exactly once per page, just at the top
 * of the window instead of the top of the body.
 *
 * With the title gone, a bare description would read as an orphaned sentence, so
 * it is styled as a lede rather than as a subtitle: muted, one step down in
 * size, and capped to a readable measure (`max-w-2xl` ≈ 70 characters) instead
 * of running the full 96rem shell width. When there is no description and no
 * actions this component renders **nothing at all** — an empty `mb-6` spacer
 * above the content would be exactly the wasted band this change set out to
 * remove.
 *
 * Layout: on >= sm the lede and the actions sit on opposite ends of a flex row;
 * on small screens the actions wrap below the lede.
 */
import { computed, onUnmounted, useSlots, watchEffect } from 'vue'

import { usePageTitle } from '@/composables/usePageTitle'

const props = withDefaults(
  defineProps<{
    title: string
    description?: string
    /** Optional id applied to the <h1>, for views that expose the title by id. */
    titleId?: string
  }>(),
  { description: undefined, titleId: undefined },
)

const slots = useSlots()
const token = Symbol('page-header')
const { claimPageTitle, releasePageTitle } = usePageTitle()

watchEffect(() => claimPageTitle(token, props.title, props.titleId))
onUnmounted(() => releasePageTitle(token))

const hasBody = computed<boolean>(
  () => Boolean(props.description) || Boolean(slots.description) || Boolean(slots.actions),
)
</script>

<template>
  <div
    v-if="hasBody"
    data-testid="page-header"
    class="w-full flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between mb-6"
  >
    <p
      v-if="description"
      data-testid="page-lede"
      class="min-w-0 max-w-2xl text-sm leading-relaxed text-gray-500 dark:text-gray-400"
    >
      {{ description }}
    </p>
    <slot name="description" />
    <div
      v-if="$slots.actions"
      class="flex flex-wrap items-center gap-2 sm:ml-auto sm:flex-shrink-0"
    >
      <slot name="actions" />
    </div>
  </div>
</template>
