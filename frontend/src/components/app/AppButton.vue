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
  primary: 'bg-violet-500 hover:bg-violet-600 text-white',
  secondary:
    'border-gray-200 dark:border-gray-700/60 hover:border-gray-300 text-gray-800 dark:text-gray-300',
  warning: 'bg-red-500 hover:bg-red-600 text-white',
  inverse: 'bg-white text-violet-600 hover:bg-gray-100',
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
