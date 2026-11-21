<script setup lang="ts">
/**
 * A filter "pill": a rounded button that toggles a dropdown panel beneath it.
 * Controlled — the parent owns the open flag via `v-model:open`, which lets a
 * filter bar keep only one pill open at a time. Behaviour (Escape closes +
 * returns focus, outside-mousedown closes, alignment, z-index) comes from
 * AppPopover; this component owns the pill button's look and the panel size.
 * The panel content is a slot.
 */
import AppPopover from './AppPopover.vue'

const props = defineProps<{
  label: string
  open: boolean
  active?: boolean
  valueLabel?: string
}>()

const emit = defineEmits<{ 'update:open': [boolean] }>()
</script>

<template>
  <AppPopover
    :open="props.open"
    align="auto"
    :panel-attrs="{ 'data-testid': 'filter-pill-panel' }"
    panel-class="absolute top-full mt-1 min-w-56 max-w-[calc(100vw-1rem)] p-3"
    @update:open="emit('update:open', $event)"
  >
    <template #trigger="{ open, toggle, triggerRef }">
      <button
        :ref="triggerRef"
        type="button"
        data-testid="filter-pill-button"
        class="inline-flex items-center gap-1 rounded-full border px-3 py-1.5 text-sm transition-colors"
        :class="
          props.active
            ? 'border-brand bg-brand-tint text-brand'
            : 'border-control-border bg-panel text-ink hover:bg-tile'
        "
        :aria-expanded="open"
        @click="toggle"
      >
        <span class="font-medium">{{ props.label }}</span>
        <span v-if="props.active && props.valueLabel" class="text-ink-secondary"
          >: {{ props.valueLabel }}</span
        >
        <span v-if="props.active" class="sr-only"> (active)</span>
        <svg class="h-3 w-3 fill-current opacity-70" viewBox="0 0 12 12" aria-hidden="true">
          <path d="M5.9 11.4L.5 6l1.4-1.4 4 4 4-4L11.3 6z" />
        </svg>
      </button>
    </template>

    <slot />
  </AppPopover>
</template>
