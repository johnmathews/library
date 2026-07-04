<script setup lang="ts">
/**
 * AppPopover — the one behavioral primitive behind the app's dropdown overlays
 * (filter pills, the dashboard-fields menu, the jobs-columns menu, the header
 * dropdowns). It owns the *behavior* and the shared panel chrome; each caller
 * keeps full control of its trigger button and the panel's positioning / width /
 * padding (via `panelClass` + fall-through attrs).
 *
 * Behavior — identical for every call site:
 *  - controlled open via `v-model:open` (a filter bar can keep one pill open by
 *    owning the flag; a self-contained menu just passes a local ref);
 *  - closes on Escape, returning focus to the trigger;
 *  - closes on an outside mousedown;
 *  - panel z-index comes from the shared `--z-popover` token (`.z-popover`).
 *
 * NOT a modal — SearchModal keeps its native <dialog>. NOT teleported — the panel
 * stays anchored in normal flow with caller-supplied `absolute`/`fixed`
 * positioning, which preserves class-based alignment (`left-0`/`right-0`) and the
 * header dropdown's bespoke responsive positioning.
 *
 * Slots:
 *  - `trigger` (scoped: `{ open, toggle, triggerRef }`) — render the trigger
 *    button here; bind `:ref="triggerRef"` on it so Escape can restore focus.
 *  - default — the panel body.
 *
 * Panel-level attributes (the panel's `data-testid`/`id`/`role`/`aria-label`)
 * are passed explicitly via `panelAttrs`. Fall-through attrs land on the root
 * element (standard Vue), so a wrapper like FilterPill can still receive a
 * `data-testid` from its own parent on the outer element.
 */
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    /** Whether the panel is open. Controlled via `v-model:open`. */
    open: boolean
    /**
     * How the panel aligns to the trigger. 'auto' mirrors FilterPill: a trigger
     * in the right half of the viewport opens leftward (right-aligned) so it
     * can't spill off the edge. 'none' lets the caller own alignment entirely
     * via `panelClass` (e.g. the header jobs dropdown's responsive positioning).
     */
    align?: 'left' | 'right' | 'auto' | 'none'
    /** Positioning / width / padding classes for the panel (caller-owned). */
    panelClass?: string
    /** Attributes bound onto the panel element (`data-testid`, `id`, `role`, …). */
    panelAttrs?: Record<string, unknown>
  }>(),
  { align: 'left', panelClass: '', panelAttrs: () => ({}) },
)

const emit = defineEmits<{ 'update:open': [boolean] }>()

const root = ref<HTMLElement | null>(null)
const triggerEl = ref<HTMLElement | null>(null)

/** Bound to the trigger button via the slot so Escape can restore focus to it. */
function setTriggerRef(el: unknown): void {
  triggerEl.value = (el as { $el?: HTMLElement } | null)?.$el ?? (el as HTMLElement | null)
}

function toggle(): void {
  emit('update:open', !props.open)
}

function close(): void {
  emit('update:open', false)
}

function onEscape(): void {
  close()
  triggerEl.value?.focus()
}

function onOutsideMousedown(event: MouseEvent): void {
  if (root.value && event.target instanceof Node && !root.value.contains(event.target)) {
    close()
  }
}

// 'auto': anchor the panel leftward when the trigger sits past the viewport
// midline, so it can't spill off the right edge on narrow screens.
const alignRight = ref(false)
function updateAlignment(): void {
  const el = root.value
  if (!el) return
  const rect = el.getBoundingClientRect()
  alignRight.value = rect.left + rect.width / 2 > window.innerWidth / 2
}

// Listen for outside mousedown only while open. `immediate` so a popover that
// mounts already-open is still dismissible (the originals only wired this on the
// closed→open transition).
watch(
  () => props.open,
  (open) => {
    if (open) {
      if (props.align === 'auto') {
        updateAlignment()
        void nextTick(updateAlignment)
      }
      document.addEventListener('mousedown', onOutsideMousedown)
    } else {
      document.removeEventListener('mousedown', onOutsideMousedown)
    }
  },
  { immediate: true },
)

onBeforeUnmount(() => document.removeEventListener('mousedown', onOutsideMousedown))

/** The alignment utility class appended to the panel. */
function alignClass(): string {
  switch (props.align) {
    case 'right':
      return 'right-0'
    case 'left':
      return 'left-0'
    case 'auto':
      return alignRight.value ? 'right-0' : 'left-0'
    default:
      return ''
  }
}
</script>

<template>
  <div ref="root" class="relative inline-flex" @keydown.escape.stop="onEscape">
    <slot name="trigger" :open="props.open" :toggle="toggle" :trigger-ref="setTriggerRef" />

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
        v-bind="props.panelAttrs"
        class="z-popover rounded-lg border border-gray-200 bg-white shadow-lg dark:border-gray-700/60 dark:bg-gray-800"
        :class="[alignClass(), props.panelClass]"
      >
        <slot />
      </div>
    </Transition>
  </div>
</template>
