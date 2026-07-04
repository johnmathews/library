<script setup lang="ts">
/**
 * Shared chart control bar (W5): time-range preset + custom from/to datepickers
 * + grouping. Presentational — the parent view owns the state (via
 * useChartsTimeframe / useChartsGrouping) and passes it in, so both the /charts
 * grid and the single-chart page drive their tiles from one control surface.
 *
 * Preset ↔ datepicker interplay lives in the composable: `select-timeframe`
 * reflects a preset's window into the from/to fields, and `set-custom` (a
 * user edit of a datepicker) flips the selection to "custom".
 *
 * Layout follows the mosaic field pattern (uppercase-xs labels, `.form-input` /
 * `.form-select`, one aligned `items-end` row) shared with the rest of the app's
 * filter bars. From/To are native `<input type="date">` — their value is already
 * ISO `yyyy-mm-dd`, matching `customFrom`/`customTo` exactly.
 */
import type { Timeframe, TimeframeOption } from '@/composables/useChartsTimeframe'
import type { ChartGrouping, GroupingOption } from '@/composables/useChartsGrouping'

defineProps<{
  timeframe: Timeframe
  timeframeOptions: TimeframeOption[]
  customFrom: string | null
  customTo: string | null
  grouping: ChartGrouping
  groupingOptions: GroupingOption[]
}>()

const emit = defineEmits<{
  'select-timeframe': [Timeframe]
  'set-custom': ['from' | 'to', string | null]
  'update:grouping': [ChartGrouping]
}>()

function onSelectTimeframe(event: Event): void {
  emit('select-timeframe', (event.target as HTMLSelectElement).value as Timeframe)
}

function onSelectGrouping(event: Event): void {
  emit('update:grouping', (event.target as HTMLSelectElement).value as ChartGrouping)
}

function onCustomDate(which: 'from' | 'to', event: Event): void {
  // Native date inputs emit ISO yyyy-mm-dd, or "" when cleared → null.
  const value = (event.target as HTMLInputElement).value
  emit('set-custom', which, value || null)
}
</script>

<template>
  <div class="flex flex-wrap items-end gap-3" data-testid="chart-controls">
    <div>
      <label class="filter-label" for="charts-timeframe">Time range</label>
      <select
        id="charts-timeframe"
        :value="timeframe"
        data-testid="charts-timeframe"
        class="form-select"
        aria-label="Shared time range across charts"
        @change="onSelectTimeframe"
      >
        <option v-for="opt in timeframeOptions" :key="opt.value" :value="opt.value">
          {{ opt.label }}
        </option>
      </select>
    </div>

    <div>
      <label class="filter-label" for="charts-range-from">From</label>
      <input
        id="charts-range-from"
        :value="customFrom ?? ''"
        type="date"
        data-testid="charts-range-from"
        class="form-input"
        aria-label="Custom range start date"
        @input="onCustomDate('from', $event)"
      />
    </div>

    <div>
      <label class="filter-label" for="charts-range-to">To</label>
      <input
        id="charts-range-to"
        :value="customTo ?? ''"
        type="date"
        data-testid="charts-range-to"
        class="form-input"
        aria-label="Custom range end date"
        @input="onCustomDate('to', $event)"
      />
    </div>

    <div>
      <label class="filter-label" for="charts-grouping">Group by</label>
      <select
        id="charts-grouping"
        :value="grouping"
        data-testid="charts-grouping"
        class="form-select"
        aria-label="Group documents into time buckets"
        @change="onSelectGrouping"
      >
        <option v-for="opt in groupingOptions" :key="opt.value" :value="opt.value">
          {{ opt.label }}
        </option>
      </select>
    </div>
  </div>
</template>
