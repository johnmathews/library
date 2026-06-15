<script setup lang="ts">
/**
 * A filter "pill": a rounded button that toggles a dropdown panel beneath it.
 * Controlled — the parent owns the open flag via `v-model:open`, which lets a
 * filter bar keep only one pill open at a time. Closes on Escape (focus
 * returns to the button) and on outside mousedown. The panel content is a slot.
 */
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps<{
  label: string
  open: boolean
  active?: boolean
  valueLabel?: string
}>()

const emit = defineEmits<{ 'update:open': [boolean] }>()

const root = ref<HTMLElement | null>(null)
const button = ref<HTMLButtonElement | null>(null)

// Panels anchor to the pill. A pill in the right half of the viewport opens its
// panel leftward (right-aligned) so it can't spill off the right edge on narrow
// screens; everything else opens rightward (left-aligned).
const alignRight = ref(false)
function updateAlignment(): void {
  const el = root.value
  if (!el) return
  const rect = el.getBoundingClientRect()
  alignRight.value = rect.left + rect.width / 2 > window.innerWidth / 2
}

function toggle(): void {
  emit('update:open', !props.open)
}

function close(): void {
  emit('update:open', false)
}

function onEscape(): void {
  close()
  button.value?.focus()
}

function onOutsideMousedown(event: MouseEvent): void {
  if (root.value && event.target instanceof Node && !root.value.contains(event.target)) {
    close()
  }
}

// Listen for outside clicks only while open.
watch(
  () => props.open,
  (open) => {
    if (open) {
      updateAlignment()
      void nextTick(updateAlignment)
      document.addEventListener('mousedown', onOutsideMousedown)
    } else {
      document.removeEventListener('mousedown', onOutsideMousedown)
    }
  },
)

onBeforeUnmount(() => document.removeEventListener('mousedown', onOutsideMousedown))
</script>

<template>
  <div ref="root" class="relative inline-flex" @keydown.escape.stop="onEscape">
    <button
      ref="button"
      type="button"
      data-testid="filter-pill-button"
      class="inline-flex items-center gap-1 rounded-full border px-3 py-1.5 text-sm transition-colors"
      :class="
        props.active
          ? 'border-violet-500 bg-violet-50 text-violet-700 dark:border-violet-500 dark:bg-violet-500/15 dark:text-violet-200'
          : 'border-gray-300 bg-white text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-gray-700/60'
      "
      :aria-expanded="props.open"
      @click="toggle"
    >
      <span class="font-medium">{{ props.label }}</span>
      <span v-if="props.active && props.valueLabel" class="text-gray-500 dark:text-gray-400"
        >: {{ props.valueLabel }}</span
      >
      <span v-if="props.active" class="sr-only"> (active)</span>
      <svg class="h-3 w-3 fill-current opacity-70" viewBox="0 0 12 12" aria-hidden="true">
        <path d="M5.9 11.4L.5 6l1.4-1.4 4 4 4-4L11.3 6z" />
      </svg>
    </button>

    <Transition
      enter-active-class="transition ease-out duration-150"
      enter-from-class="opacity-0 -translate-y-1"
      enter-to-class="opacity-100 translate-y-0"
      leave-active-class="transition ease-out duration-150"
      leave-from-class="opacity-100 translate-y-0"
      leave-to-class="opacity-0 -translate-y-1"
    >
      <div
        v-if="props.open"
        data-testid="filter-pill-panel"
        class="absolute top-full z-20 mt-1 min-w-56 max-w-[calc(100vw-1rem)] rounded-lg border border-gray-200 bg-white p-3 shadow-lg dark:border-gray-700/60 dark:bg-gray-800"
        :class="alignRight ? 'right-0' : 'left-0'"
      >
        <slot />
      </div>
    </Transition>
  </div>
</template>
