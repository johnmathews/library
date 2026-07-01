<script setup lang="ts">
/**
 * Single-chart page (route `/charts/:seriesId`): a shareable, deep-linkable view
 * of one recurring series. Fetches GET /api/charts/{seriesId} and renders one
 * editable `SeriesChartTile` (title/description editable, membership editable).
 * The seriesId is the stable `{sender_id}-{kind_id}-{currency|none}` id.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter, RouterLink } from 'vue-router'
import { fetchChart, type DocumentSeries } from '@/api/documents'
import SeriesChartTile from '@/components/SeriesChartTile.vue'
import ChartControls from '@/components/charts/ChartControls.vue'
import { useChartsTimeframe } from '@/composables/useChartsTimeframe'
import { useChartsGrouping } from '@/composables/useChartsGrouping'
import { downloadImage, downloadPdf, copyShareUrl, type ImageFormat } from '@/utils/chartExport'

const route = useRoute()
const router = useRouter()

// Handle to the tile so we can grab its rendered <canvas> for export (W6).
const tileRef = ref<{ getChartCanvas: () => HTMLCanvasElement | null } | null>(null)

// Same shared time-range + grouping as the grid (persisted per browser), so a
// chart opens with the last-used view.
const {
  timeframe,
  customFrom,
  customTo,
  options: timeframeOptions,
  bounds: axisBounds,
  selectTimeframe,
  setCustom,
} = useChartsTimeframe()
const { grouping, options: groupingOptions } = useChartsGrouping()

// After the series is deleted there is nothing to show here — return to the grid.
function onDeleted(): void {
  router.push('/charts')
}

// --- Export & share (W6) ----------------------------------------------------
const copied = ref(false)

// Filename/heading for exports: the user title, else the derived label.
const chartTitle = computed<string>(() => {
  const c = chart.value
  if (!c) return 'chart'
  return c.title?.trim() ? c.title : `${c.sender} · ${c.cadence} series`
})

function exportImage(format: ImageFormat): void {
  const canvas = tileRef.value?.getChartCanvas()
  if (canvas) downloadImage(canvas, format, chartTitle.value)
}

function exportPdf(): void {
  const canvas = tileRef.value?.getChartCanvas()
  if (canvas) downloadPdf(canvas, chartTitle.value, chartTitle.value)
}

async function share(): Promise<void> {
  await copyShareUrl()
  copied.value = true
  window.setTimeout(() => (copied.value = false), 2000)
}
const chart = ref<DocumentSeries | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  chart.value = null
  const id = String(route.params.seriesId)
  try {
    chart.value = await fetchChart(id)
  } catch {
    // Any failure (404 for an unknown/unchartable series, network, etc.) lands
    // on the same not-found surface.
    error.value = 'This chart could not be found. It may have too few documents to display.'
  } finally {
    loading.value = false
  }
}

onMounted(load)
// Re-fetch when navigating between single-chart pages without a full remount.
watch(() => route.params.seriesId, load)
</script>

<template>
  <div id="series-chart-view">
    <RouterLink
      to="/charts"
      data-testid="series-chart-back"
      class="inline-block mb-4 text-sm text-violet-600 hover:text-violet-700 dark:text-violet-400 dark:hover:text-violet-300 hover:underline"
    >
      &larr; All charts
    </RouterLink>

    <p
      v-if="loading"
      data-testid="series-chart-loading"
      class="text-gray-600 dark:text-gray-300"
    >
      Loading chart…
    </p>

    <div
      v-else-if="error"
      data-testid="series-chart-error"
      role="alert"
      class="bg-white dark:bg-gray-800 border-l-4 border-red-500 rounded-lg px-4 py-3 shadow-xs text-gray-700 dark:text-gray-200"
    >
      {{ error }}
    </div>

    <div v-else-if="chart">
      <!-- Toolbar: shared controls on the left, export/share on the right (W6). -->
      <div class="mb-4 flex flex-wrap items-end justify-between gap-4">
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

        <!-- Export & share the chart (W6). -->
        <div class="flex flex-wrap items-center gap-2" data-testid="chart-export">
          <button
            type="button"
            data-testid="chart-export-pdf"
            class="btn-sm border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700/50"
            @click="exportPdf"
          >
            Download PDF
          </button>
          <button
            type="button"
            data-testid="chart-export-jpeg"
            class="btn-sm border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700/50"
            @click="exportImage('jpeg')"
          >
            JPEG
          </button>
          <button
            type="button"
            data-testid="chart-export-png"
            class="btn-sm border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700/50"
            @click="exportImage('png')"
          >
            PNG
          </button>
          <button
            type="button"
            data-testid="chart-share"
            class="btn-sm border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700/50"
            @click="share"
          >
            {{ copied ? 'Link copied' : 'Copy link' }}
          </button>
        </div>
      </div>

      <SeriesChartTile
        ref="tileRef"
        :series="chart"
        editable
        size="large"
        :axis-min="axisBounds.min"
        :axis-max="axisBounds.max"
        :grouping="grouping"
        @changed="load"
        @deleted="onDeleted"
      />
    </div>
  </div>
</template>
