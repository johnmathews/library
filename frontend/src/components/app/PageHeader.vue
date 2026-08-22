<script setup lang="ts">
/**
 * Shared page header: an optional one-line **lede**, an optional left-aligned
 * `controls` slot (the view's filter/control bar), and an optional
 * right-aligned `actions` slot.
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
 * of running the full 96rem shell width. When there is no description, no
 * controls and no actions this component renders **nothing at all** — an empty
 * `mb-6` spacer above the content would be exactly the wasted band this change
 * set out to remove.
 *
 * ## The `controls` slot (§5)
 *
 * `/charts`, `/jobs` and `/matters` each used to open a *second* full-width band
 * below this header just to hold their filter bar, while the header row beside
 * it sat mostly empty. Passing that bar through `#controls` merges the two into
 * one toolbar — controls left, page commands right — which is the conventional
 * arrangement and reclaims a band on every such view.
 *
 * Two rules make it work, and both are load-bearing:
 *
 * 1. **The merge is a container query, not a viewport one.** The row only fits
 *    when the *content column* is wide enough, and that column is the viewport
 *    minus a sidebar the user can collapse independently — so `lg:` is wrong in
 *    both directions (a 1280px viewport with the sidebar collapsed has more room
 *    than one at 1440px with it open). The header declares itself an `@container`
 *    and the children query it at `@5xl` (64rem of *container* width). That
 *    number is measured, not chosen — see `e2e/header-toolbar.spec.ts`, which
 *    asserts both that the row merges above it and that it stacks below.
 * 2. **Below the threshold the groups stack, reproducing the old layout** —
 *    controls full width, actions full width beneath. Nothing regresses on a
 *    phone; the merge is purely a wide-screen gain.
 *
 * The row switches from `items-center` to `items-end` when controls are present:
 * a lede-and-buttons row wants centres aligned, but a row of labelled fields
 * wants the *inputs* sharing a bottom edge (§5), and the buttons join that edge.
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

const hasControls = computed<boolean>(() => Boolean(slots.controls))

const hasBody = computed<boolean>(
  () =>
    Boolean(props.description) ||
    Boolean(slots.description) ||
    Boolean(slots.actions) ||
    hasControls.value,
)

// Two shapes for one row. Without controls this is byte-for-byte the old
// lede/actions row, so every view that predates the slot is untouched.
const rowClass = computed<string>(() =>
  hasControls.value
    ? 'flex flex-wrap items-end gap-x-4 gap-y-3'
    : 'flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between',
)

const actionsClass = computed<string>(() =>
  hasControls.value
    ? 'flex flex-wrap items-center gap-2 w-full @5xl:w-auto @5xl:ml-auto'
    : 'flex flex-wrap items-center gap-2 sm:ml-auto sm:flex-shrink-0',
)
</script>

<template>
  <div v-if="hasBody" data-testid="page-header" class="@container w-full mb-6">
    <p
      v-if="description"
      data-testid="page-lede"
      :class="[
        'min-w-0 max-w-2xl text-sm leading-relaxed text-gray-500 dark:text-gray-400',
        // With controls the row below is a toolbar, so the lede takes its own
        // line rather than competing with a field for horizontal space.
        hasControls ? 'mb-3' : '',
      ]"
    >
      {{ description }}
    </p>
    <!-- With controls the row is a toolbar, so slotted description content
         takes its own line above it, exactly as the `description` prop does.
         Rendering it only in the row would silently drop it for any view that
         passed both slots — and `hasBody` counts it, so the header would render
         *because* of content that never appeared. -->
    <div v-if="hasControls && $slots.description" class="mb-3">
      <slot name="description" />
    </div>
    <div :class="rowClass">
      <slot v-if="!hasControls" name="description" />
      <div
        v-if="hasControls"
        data-testid="page-header-controls"
        class="min-w-0 w-full @5xl:w-auto"
      >
        <slot name="controls" />
      </div>
      <div v-if="$slots.actions" data-testid="page-header-actions" :class="actionsClass">
        <slot name="actions" />
      </div>
    </div>
  </div>
</template>
