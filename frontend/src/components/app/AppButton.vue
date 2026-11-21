<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, type RouteLocationRaw } from 'vue-router'

const props = withDefaults(
  defineProps<{
    variant?: 'primary' | 'secondary' | 'warning' | 'inverse'
    /** Base sizing class: default `.btn`, `sm` → `.btn-sm`, `lg` → `.btn-lg`. */
    size?: 'sm' | 'lg'
    type?: 'submit' | 'button' | 'reset'
    to?: RouteLocationRaw
    href?: string
    /** Link target, e.g. `_blank` to open in a new tab. Applies only to the
     * `to` (RouterLink) and `href` (anchor) forms; ignored for the `<button>`.
     * `_blank` automatically gets `rel="noopener"`. */
    target?: string
    disabled?: boolean
    preventDoubleClick?: boolean
  }>(),
  { variant: 'primary', type: 'submit', disabled: false, preventDoubleClick: false },
)

const emit = defineEmits<{ (e: 'click', event: MouseEvent): void }>()

const variantClasses: Record<NonNullable<typeof props.variant>, string> = {
  // Design system: Button. One primary per screen; everything else is outline.
  primary: 'bg-brand-fill text-on-brand hover:brightness-110',
  secondary: 'bg-panel border-control-border text-ink hover:bg-tile',
  // No red button (design system: Button): red means "failed". A destructive
  // action opens a confirmation, whose confirming button is the primary.
  // `warning` stays as a name so call sites need not change, and renders as
  // the outline button.
  warning: 'bg-panel border-control-border text-ink hover:bg-tile',
  inverse: 'bg-panel text-brand hover:bg-tile',
}

const sizeClass = computed(() =>
  props.size === 'sm' ? 'btn-sm' : props.size === 'lg' ? 'btn-lg' : 'btn',
)

const classes = computed(() => [
  sizeClass.value,
  variantClasses[props.variant],
  { 'pointer-events-none opacity-60': props.disabled },
])

// Opening a new tab without `rel="noopener"` lets the opened page reach back
// through `window.opener`; pair the two so callers can't forget.
const linkRel = computed(() => (props.target === '_blank' ? 'noopener' : undefined))

let lastClick = 0

function onClick(event: MouseEvent): void {
  if (props.preventDoubleClick) {
    const now = Date.now()
    if (now - lastClick < 1000) return
    lastClick = now
  }
  emit('click', event)
}
</script>

<template>
  <RouterLink
    v-if="props.to"
    :to="props.to"
    :target="props.target"
    :rel="linkRel"
    :class="classes"
    :aria-disabled="props.disabled || undefined"
    @click="props.disabled ? $event.preventDefault() : onClick($event)"
  >
    <slot />
  </RouterLink>
  <a
    v-else-if="props.href"
    :href="props.href"
    role="button"
    :target="props.target"
    :rel="linkRel"
    :class="classes"
    :aria-disabled="props.disabled || undefined"
    @click="props.disabled ? $event.preventDefault() : onClick($event)"
  >
    <slot />
  </a>
  <button
    v-else
    :type="props.type"
    :class="classes"
    :disabled="props.disabled"
    :aria-disabled="props.disabled || undefined"
    @click="onClick"
  >
    <slot />
  </button>
</template>
