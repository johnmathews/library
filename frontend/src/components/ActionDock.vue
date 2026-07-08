<script setup lang="ts">
/**
 * The document-detail page's floating action dock: the hero's primary
 * actions (Ask, and the shared metadata Edit/Done toggle) kept reachable
 * once the hero itself has scrolled out of view. Extracted from
 * `DocumentDetailView` (formerly the inline "island") so its screen position
 * can be driven by the user's `dockPosition` preference (Settings →
 * Appearance) rather than being hard-coded bottom-right.
 *
 * Positioning: the dock is `sticky` (not `fixed`) so it stays within the
 * document-detail page's own scroll container (`#app-content`,
 * `overflow-y-auto` — see `DefaultLayout.vue`) rather than the viewport; that
 * keeps it clipped to the content area instead of floating over the sidebar.
 * `fixed` can't be scoped to the content column here: the only ancestor that
 * could establish a containing block for it *is* the scroll container, and a
 * `fixed` element inside its own scroll container scrolls away with the
 * content (verified) — so sticky is the correct tool.
 *
 * A single sticky element can only pin in a direction that has scrollable
 * content beyond it: `bottom-0` pins to the viewport bottom only when mounted
 * *late* in the flow (content above scrolls past it), and `top-16` pins below
 * the header only when mounted *early* in the flow (content below scrolls up
 * past it). It cannot do both from one slot — so `DocumentDetailView` mounts
 * this component in the matching slot for the chosen position (early for
 * `top-*`, late for `bottom-*`).
 *
 * The sticky element itself is a zero-height rail (`h-0`); the visible pill
 * row is absolutely positioned within it. That way the rail reserves no space
 * in the flow, so mounting/unmounting the dock (it appears only once the hero
 * scrolls off) never shifts the surrounding content. `top-16` clears
 * `AppHeader`'s fixed 4rem (`h-16`) height; the row is `pointer-events-none`
 * so only the pill (`pointer-events-auto`) is interactive.
 */
import { computed } from 'vue'
import { AppButton } from '@/components/app'
import { useAuthStore } from '@/stores/auth'
import { useMetadataEditMode } from '@/composables/useMetadataEditMode'

defineProps<{
  askHref: string
}>()

const { editMode, toggle } = useMetadataEditMode()

const dockPosition = computed(() => useAuthStore().dockPosition)
const isTop = computed(() => dockPosition.value.startsWith('top'))

// Rail sticks to `top-16` (clearing the 4rem header) or `bottom-0`; the pill
// row anchors to the matching edge of the zero-height rail, offset inward
// (`top-4`/`bottom-4`) so the pill floats with a comfortable gap rather than
// squished flush against the header edge / viewport bottom.
const edgeClass = computed(() => (isTop.value ? 'top-16' : 'bottom-0'))
const rowAnchorClass = computed(() => (isTop.value ? 'top-4' : 'bottom-4'))
const justifyClass = computed(() =>
  dockPosition.value.endsWith('left')
    ? 'justify-start'
    : dockPosition.value.endsWith('right')
      ? 'justify-end'
      : 'justify-center',
)
</script>

<template>
  <div
    data-testid="action-dock-wrapper"
    class="sticky z-40 h-0"
    :class="edgeClass"
  >
    <div
      data-testid="action-dock-row"
      class="pointer-events-none absolute inset-x-0 flex px-4 sm:px-6 lg:px-8"
      :class="[rowAnchorClass, justifyClass]"
    >
      <div
        data-testid="action-dock"
        class="pointer-events-auto flex items-center gap-2 rounded-full border border-gray-200 bg-white/95 p-1.5 shadow-lg backdrop-blur dark:border-gray-700/60 dark:bg-gray-800/95"
      >
      <button
        type="button"
        class="btn-sm rounded-full border-gray-200 dark:border-gray-700/60 hover:border-gray-300 text-gray-700 dark:text-gray-300 gap-1.5"
        :class="editMode ? 'bg-violet-50 text-violet-700 border-violet-200 dark:bg-violet-500/15 dark:text-violet-300' : ''"
        data-testid="action-dock-edit-toggle"
        :aria-pressed="editMode"
        @click="toggle"
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          fill="none"
          viewBox="0 0 24 24"
          stroke-width="1.5"
          stroke="currentColor"
          class="w-4 h-4"
          aria-hidden="true"
        >
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            d="m16.862 4.487 1.687-1.688a1.875 1.875 0 1 1 2.652 2.652L10.582 16.07a4.5 4.5 0 0 1-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 0 1 1.13-1.897l8.932-8.931Zm0 0L19.5 7.125"
          />
        </svg>
        {{ editMode ? 'Done' : 'Edit' }}
      </button>
      <AppButton
        :href="askHref"
        target="_blank"
        size="sm"
        class="rounded-full gap-1.5"
        data-testid="action-dock-ask"
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          fill="none"
          viewBox="0 0 24 24"
          stroke-width="1.5"
          stroke="currentColor"
          class="w-4 h-4"
          aria-hidden="true"
        >
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            d="M8.625 12a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H8.25m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H12m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0h-.375M21 12c0 4.556-4.03 8.25-9 8.25a9.764 9.764 0 0 1-2.555-.337A5.972 5.972 0 0 1 5.41 20.97a5.969 5.969 0 0 1-.474-.065 4.48 4.48 0 0 0 .978-2.025c.09-.457-.133-.901-.467-1.226C3.93 16.178 3 14.189 3 12c0-4.556 4.03-8.25 9-8.25s9 3.694 9 8.25Z"
          />
        </svg>
        Ask
      </AppButton>
      </div>
    </div>
  </div>
</template>
