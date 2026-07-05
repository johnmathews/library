<script setup lang="ts">
/**
 * Document-detail trend chart: the recurring series this document belongs to,
 * rendered with the SAME control bar (time range / from / to / group by) and
 * defaults as the /charts pages. Controls persist under their OWN storage keys
 * so they never fight the global dashboard controls.
 *
 * The endpoint returns ALL points; the timeframe window is applied client-side
 * (display-only, matching /charts) and the grouping + axis bounds are forwarded
 * to SeriesChartTile, which owns rendering, grouping and click-through.
 */
import { computed, onMounted, ref } from 'vue'
import { useStorage } from '@vueuse/core'
import { fetchDocumentSeries, type DocumentSeries, type SeriesPoint } from '@/api/documents'
import SeriesChartTile from './SeriesChartTile.vue'
import ChartControls from './charts/ChartControls.vue'
import { useChartsTimeframe } from '@/composables/useChartsTimeframe'
import { GROUPING_OPTIONS, type ChartGrouping } from '@/composables/useChartsGrouping'

const props = defineProps<{ documentId: number }>()
const series = ref<DocumentSeries | null>(null)

// Same time-range + grouping surface as /charts, but persisted under
// document-detail-specific keys so it stays independent of the dashboard.
const {
  timeframe,
  customFrom,
  customTo,
  options: timeframeOptions,
  bounds: axisBounds,
  selectTimeframe,
  setCustom,
} = useChartsTimeframe({
  timeframe: 'library:doc-series-timeframe',
  customFrom: 'library:doc-series-custom-from',
  customTo: 'library:doc-series-custom-to',
})
// Grouping is a single persisted ref; instantiate it directly against a
// doc-specific key (default 'month', matching /charts) rather than the shared
// composable, so the global grouping choice is untouched.
const grouping = useStorage<ChartGrouping>('library:doc-series-grouping', 'month')
const groupingOptions = GROUPING_OPTIONS

// Client-side timeframe filter (display-only). ISO yyyy-mm-dd dates compare
// lexicographically, so plain string bounds work. A null bound is open.
const windowedPoints = computed<SeriesPoint[]>(() => {
  const pts = series.value?.points ?? []
  const { min, max } = axisBounds.value
  return pts.filter((p) => (!min || p.date >= min) && (!max || p.date <= max))
})

// The series handed to the tile, narrowed to the active window. The tile still
// owns grouping (via the `grouping` prop) and axis clamping (via axis-min/max).
const displaySeries = computed<DocumentSeries | null>(() =>
  series.value ? { ...series.value, points: windowedPoints.value } : null,
)

const hasWindowedData = computed<boolean>(() => windowedPoints.value.length > 0)

async function load(): Promise<void> {
  try {
    const data = await fetchDocumentSeries(props.documentId)
    series.value = data.status === 'ok' ? data : null
  } catch {
    series.value = null
  }
}

onMounted(load)
</script>

<template>
  <div v-if="series" class="mt-4">
    <div data-testid="doc-series-controls" class="mb-4">
      <ChartControls
        :timeframe="timeframe"
        :timeframe-options="timeframeOptions"
        :custom-from="customFrom"
        :custom-to="customTo"
        :grouping="grouping"
        :grouping-options="groupingOptions"
        @select-timeframe="selectTimeframe"
        @set-custom="setCustom"
        @update:grouping="grouping = $event"
      />
    </div>

    <SeriesChartTile
      v-if="hasWindowedData && displaySeries"
      :series="displaySeries"
      :highlight-document-id="props.documentId"
      :axis-min="axisBounds.min"
      :axis-max="axisBounds.max"
      :grouping="grouping"
      editable
      @changed="load"
    />

    <p
      v-else
      data-testid="doc-series-empty"
      class="rounded-md border border-gray-200 dark:border-gray-700 px-4 py-6 text-sm text-gray-500 dark:text-gray-400"
    >
      No documents in the selected time range.
    </p>
  </div>
</template>
