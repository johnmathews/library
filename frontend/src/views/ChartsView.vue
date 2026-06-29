<script setup lang="ts">
/**
 * Charts dashboard (route `/charts`): one tile per recurring (sender, kind)
 * series with enough documents to summarise. Each tile shows the trend chart,
 * the cached LLM description, and the editable list of documents that make up
 * the series (add/remove persists as an override; see W8). The data comes from
 * GET /api/charts; a membership change refetches the whole grid.
 */
import { onMounted, ref } from 'vue'
import { fetchCharts, seriesId, type DocumentSeries } from '@/api/documents'
import SeriesChartTile from '@/components/SeriesChartTile.vue'

const series = ref<DocumentSeries[]>([])
const loading = ref(true)
const error = ref<string | null>(null)

// Stable per-tile key + deep-link id; shared with the backend (seriesId()).
const seriesKey = seriesId

async function load(): Promise<void> {
  error.value = null
  try {
    const response = await fetchCharts()
    series.value = response.series
  } catch {
    error.value = 'Could not load charts. Try refreshing the page.'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div id="charts-view">
    <h1 class="text-2xl md:text-3xl text-gray-800 dark:text-gray-100 font-bold mb-6">Charts</h1>

    <p v-if="loading" data-testid="charts-loading" class="text-gray-600 dark:text-gray-300">
      Loading charts…
    </p>

    <div
      v-else-if="error"
      data-testid="charts-error"
      role="alert"
      class="bg-white dark:bg-gray-800 border-l-4 border-red-500 rounded-lg px-4 py-3 shadow-xs text-gray-700 dark:text-gray-200"
    >
      {{ error }}
    </div>

    <p
      v-else-if="series.length === 0"
      data-testid="charts-empty"
      class="text-gray-600 dark:text-gray-300"
    >
      No recurring series yet. Once a sender has several documents of the same kind with amounts,
      its trend will appear here.
    </p>

    <div
      v-else
      data-testid="charts-grid"
      class="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-6"
    >
      <SeriesChartTile
        v-for="s in series"
        :key="seriesKey(s)"
        :series="s"
        editable
        detail-link
        @changed="load"
      />
    </div>
  </div>
</template>
