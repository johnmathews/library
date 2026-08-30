<script setup lang="ts">
/**
 * The drill-through panel shell (spec §4.6, §4.13).
 *
 * A native `<dialog>`, the same convention as `SearchModal.vue` and
 * `ConfirmDialog.vue`: `showModal()` gives focus containment, Escape-to-close
 * and an inert background for free, rather than hand-rolling any of that a
 * third time. This component owns exactly that plus the side-panel-vs-
 * bottom-sheet presentation — it fetches nothing. Its default slot holds one
 * of `DrillCellBody` / `DrillBucketBody` / `DrillOtherBody`, which own their
 * own data and know nothing about how they are presented; that split is the
 * point of this component existing at all — one copy of the responsive
 * behaviour, three bodies that cannot drift from it.
 *
 * `sheet` arrives as a resolved boolean rather than a container query this
 * component runs itself: the decision belongs to the *workspace's* content
 * column (measured against the `@3xl` / 48rem threshold, spec §4.13), and a
 * `<dialog>` is top-layer, out-of-flow content with no ambient container of
 * its own to query. `data-presentation` mirrors that decision as a plain DOM
 * attribute so a test asserts the outcome, not a class list.
 */
import { ref, watch } from 'vue'

const props = defineProps<{
  open: boolean
  title: string
  sheet: boolean
}>()

const emit = defineEmits<{ close: [] }>()

const dialog = ref<HTMLDialogElement | null>(null)

// The element that had focus before the panel opened; native dialogs restore
// focus in most browsers, but jsdom and some older engines do not — doing it
// explicitly keeps the contract deterministic (same rationale as SearchModal).
let opener: HTMLElement | null = null

watch(
  () => props.open,
  (open) => {
    const el = dialog.value
    if (!el) return
    if (open) {
      opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
      if (!el.open) el.showModal()
    } else if (el.open) {
      el.close()
    }
  },
)

/** Native `close`: fires alike for ESC, a backdrop click and the Close
 * button (which calls `dialog.close()` below) — one path emits `close` for
 * all three, so the parent only ever handles it in one place. */
function onClose(): void {
  opener?.focus()
  opener = null
  emit('close')
}

function requestClose(): void {
  dialog.value?.close()
}

/** A click on the dialog element itself (not its content) is a backdrop click. */
function onDialogClick(event: MouseEvent): void {
  if (event.target === dialog.value) requestClose()
}
</script>

<template>
  <dialog
    ref="dialog"
    class="app-drill-panel bg-white text-left shadow-lg dark:bg-gray-800"
    :data-presentation="sheet ? 'sheet' : 'panel'"
    aria-labelledby="drill-panel-title"
    data-testid="drill-panel"
    @close="onClose"
    @click="onDialogClick"
  >
    <div class="flex h-full min-h-0 flex-col">
      <div
        class="flex shrink-0 items-center justify-between gap-3 border-b border-gray-200 px-5 py-4 dark:border-gray-700/60"
      >
        <h2
          id="drill-panel-title"
          class="min-w-0 truncate text-lg font-semibold text-gray-800 dark:text-gray-100"
          data-testid="drill-title"
        >
          {{ title }}
        </h2>
        <button
          type="button"
          class="shrink-0 text-sm text-gray-500 underline dark:text-gray-400"
          data-testid="drill-close"
          @click="requestClose"
        >
          Close
        </button>
      </div>
      <div class="@container min-h-0 flex-1 overflow-y-auto p-5" data-testid="drill-body">
        <slot />
      </div>
    </div>
  </dialog>
</template>
