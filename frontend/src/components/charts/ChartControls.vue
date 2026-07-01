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
 */
import AppDateInput from '@/components/app/AppDateInput.vue'
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
</script>

<template>
  <div class="flex flex-wrap items-end gap-4" data-testid="chart-controls">
    <label class="flex flex-col gap-1 text-sm text-gray-600 dark:text-gray-300">
      <span class="font-medium">Time range</span>
      <select
        :value="timeframe"
        data-testid="charts-timeframe"
        class="form-select text-sm"
        aria-label="Shared time range across charts"
        @change="onSelectTimeframe"
      >
        <option v-for="opt in timeframeOptions" :key="opt.value" :value="opt.value">
          {{ opt.label }}
        </option>
      </select>
    </label>

    <AppDateInput
      id="chart-range-from"
      legend="From"
      data-testid="charts-range-from"
      :model-value="customFrom"
      @update:model-value="(v) => emit('set-custom', 'from', v)"
    />
    <AppDateInput
      id="chart-range-to"
      legend="To"
      data-testid="charts-range-to"
      :model-value="customTo"
      @update:model-value="(v) => emit('set-custom', 'to', v)"
    />

    <label class="flex flex-col gap-1 text-sm text-gray-600 dark:text-gray-300">
      <span class="font-medium">Group by</span>
      <select
        :value="grouping"
        data-testid="charts-grouping"
        class="form-select text-sm"
        aria-label="Group documents into time buckets"
        @change="onSelectGrouping"
      >
        <option v-for="opt in groupingOptions" :key="opt.value" :value="opt.value">
          {{ opt.label }}
        </option>
      </select>
    </label>
  </div>
</template>
