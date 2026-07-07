<script setup lang="ts">
/**
 * Confirmation dialog for destructive, irreversible actions (e.g. permanently
 * deleting a document from Recently Deleted).
 *
 * Built on the native `<dialog>` element via `showModal()` — the same app
 * convention as SearchModal — so focus containment, ESC-to-close and the
 * `::backdrop` come for free. The parent owns the `open` state: bind `:open`
 * and flip it to false in the `@confirm`/`@cancel` handlers. Escape, a backdrop
 * click, or the Cancel button all emit `cancel`; the Cancel button is focused
 * on open so a stray Enter never triggers the destructive default.
 */
import { ref, watch } from 'vue'
import AppButton from './AppButton.vue'

const props = withDefaults(
  defineProps<{
    open: boolean
    title: string
    message?: string
    confirmLabel?: string
    /** Disables the confirm button and shows a pending label while the action runs. */
    busy?: boolean
  }>(),
  { message: '', confirmLabel: 'Delete', busy: false },
)

const emit = defineEmits<{ (e: 'confirm'): void; (e: 'cancel'): void }>()

const dialog = ref<HTMLDialogElement | null>(null)
const cancelButton = ref<HTMLButtonElement | null>(null)

watch(
  () => props.open,
  (open) => {
    const el = dialog.value
    if (!el) return
    if (open) {
      if (!el.open) el.showModal()
      // Focus Cancel, not Confirm: the safe default for a destructive dialog.
      void cancelButton.value?.focus()
    } else if (el.open) {
      el.close()
    }
  },
)

/** Cancel via the button, backdrop, or ESC — a no-op while a confirmed action
 * is in flight (`busy`), so a late Cancel can't close the dialog as if nothing
 * will happen while the request still completes underneath. */
function cancel(): void {
  if (props.busy) return
  emit('cancel')
}

/** Native `close` (ESC or a programmatic close): treat a user-driven ESC while
 * still open as a cancel. When the parent has already flipped `open` to false
 * (after confirm/cancel), skip — the close is our own and would double-fire. */
function onClose(): void {
  if (props.open) cancel()
}

/** ESC fires `cancel` before `close`; block it while busy so the dialog stays
 * open and the in-flight action visibly owns the moment. */
function onNativeCancel(event: Event): void {
  if (props.busy) event.preventDefault()
}

/** A click on the dialog element itself (not its content) is a backdrop click. */
function onDialogClick(event: MouseEvent): void {
  if (event.target === dialog.value) cancel()
}
</script>

<template>
  <dialog
    ref="dialog"
    class="app-confirm-dialog bg-white dark:bg-gray-800 shadow-lg p-0 backdrop:bg-gray-900/40"
    :aria-labelledby="'confirm-dialog-title'"
    data-testid="confirm-dialog"
    @close="onClose"
    @cancel="onNativeCancel"
    @click="onDialogClick"
  >
    <div class="p-5 max-w-sm">
      <h2
        id="confirm-dialog-title"
        class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-2"
      >
        {{ props.title }}
      </h2>
      <p v-if="props.message" class="text-sm text-gray-600 dark:text-gray-300 mb-5">
        {{ props.message }}
      </p>
      <div class="flex items-center justify-end gap-3">
        <button
          ref="cancelButton"
          type="button"
          class="text-sm text-gray-500 dark:text-gray-400 underline disabled:opacity-50"
          :disabled="props.busy"
          data-testid="confirm-cancel"
          @click="cancel"
        >
          Cancel
        </button>
        <AppButton
          variant="warning"
          type="button"
          :disabled="props.busy"
          data-testid="confirm-accept"
          @click="emit('confirm')"
        >
          {{ props.busy ? 'Deleting…' : props.confirmLabel }}
        </AppButton>
      </div>
    </div>
  </dialog>
</template>
