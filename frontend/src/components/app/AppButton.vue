<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'

const props = withDefaults(
  defineProps<{
    variant?: 'primary' | 'secondary' | 'warning' | 'inverse'
    type?: 'submit' | 'button' | 'reset'
    to?: string
    href?: string
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

const classes = computed(() => ['btn', variantClasses[props.variant]])

function onClick(event: MouseEvent): void {
  emit('click', event)
}
</script>

<template>
  <RouterLink v-if="props.to" :to="props.to" :class="classes" @click="onClick">
    <slot />
  </RouterLink>
  <a v-else-if="props.href" :href="props.href" role="button" :class="classes" @click="onClick">
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
