<script setup lang="ts">
/**
 * The `/charts` legend (spec §4.7).
 *
 * A display filter over the same `bands` prop `SpendingChart.vue` draws
 * from — it never derives a colour of its own, it renders `band.light` /
 * `band.dark` verbatim, exactly as the chart does. Click isolates a band
 * (`isolate`); modifier-click (Cmd or Ctrl) excludes just that one
 * (`exclude`). Isolation and exclusion are both display filters the parent
 * applies as the `hidden` set on this component *and* on `SpendingChart` —
 * this component never hides its own rows for a hidden band, it only marks
 * them, because removing a band from the legend would leave no way to bring
 * it back (§4.7's "an isolate that silently rewrote the headline" warning
 * is about the total; the symmetric mistake here would be losing the undo).
 *
 * `bands` is `[]` for an unsplit chart (no fold happened, so there is
 * nothing to name) and the legend renders nothing at all — the chart's own
 * name already says what is plotted, and one colour needs no legend.
 */
import { computed } from 'vue'
import { useDark } from '@vueuse/core'
import type { Band, OTHER_VALUE } from '@/spending/palette'
import { formatMoney, fromCents } from '@/spending/money'

const props = defineProps<{
  bands: Band[]
  hidden: Set<string | null | symbol>
  currency: string
  compact?: boolean
}>()

const emit = defineEmits<{
  isolate: [key: string | null | typeof OTHER_VALUE]
  exclude: [key: string | null | typeof OTHER_VALUE]
  reset: []
}>()

// Same call as ThemeToggle.vue / SpendingChart.vue — one source of truth for
// "is dark active", reactive off the `html` class.
const isDark = useDark({ selector: 'html' })

function swatchColour(band: Band): string {
  return isDark.value ? band.dark : band.light
}

function isHidden(band: Band): boolean {
  return props.hidden.has(band.value)
}

function amountOf(band: Band): string {
  return formatMoney(fromCents(band.totalCents), props.currency)
}

// The Other row folds several split values into one bucket; naming its
// members is what keeps them identifiable once they no longer have a row
// of their own (§4.12 "Other drills in two steps" — this is step zero,
// reading without drilling at all).
function memberNames(band: Band): string {
  return band.members.map((m) => m.label).join(', ')
}

function onRowClick(band: Band, event: MouseEvent): void {
  if (event.metaKey || event.ctrlKey) {
    emit('exclude', band.value)
  } else {
    emit('isolate', band.value)
  }
}

const anyHidden = computed(() => props.hidden.size > 0)
</script>

<template>
  <div
    v-if="bands.length > 0"
    class="flex flex-col gap-1"
    data-testid="spending-legend"
  >
    <button
      v-for="band in bands"
      :key="typeof band.value === 'symbol' ? 'other' : (band.value ?? 'null')"
      type="button"
      class="flex items-center gap-2 rounded-lg px-2 py-1 text-left transition hover:bg-gray-100 dark:hover:bg-gray-700/50"
      :class="compact ? 'text-xs' : 'text-sm'"
      :aria-pressed="!isHidden(band)"
      data-testid="spending-legend-row"
      @click="onRowClick(band, $event)"
    >
      <span
        class="h-3 w-3 shrink-0 rounded-full"
        :class="{ 'opacity-40': isHidden(band) }"
        :style="{ backgroundColor: swatchColour(band) }"
        data-testid="spending-legend-swatch"
      />
      <span
        class="min-w-0 flex-1 truncate text-gray-800 dark:text-gray-100"
        :class="{ 'opacity-60': isHidden(band) }"
      >
        {{ band.label }}
        <span
          v-if="band.isOther && !compact"
          class="block truncate text-xs text-gray-500 dark:text-gray-400"
          data-testid="spending-legend-other-members"
        >
          {{ memberNames(band) }}
        </span>
      </span>
      <span
        class="shrink-0 font-medium tabular-nums text-gray-800 dark:text-gray-100"
        :class="{ 'opacity-60': isHidden(band) }"
      >
        {{ amountOf(band) }}
      </span>
    </button>
    <button
      v-if="anyHidden"
      type="button"
      class="self-start text-xs font-medium text-violet-600 hover:underline dark:text-violet-400"
      data-testid="spending-legend-reset"
      @click="emit('reset')"
    >
      Show all
    </button>
  </div>
</template>
