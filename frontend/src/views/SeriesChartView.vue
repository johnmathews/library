<script setup lang="ts">
/**
 * Single-chart page (route `/charts/:seriesId`): a shareable, deep-linkable view
 * of one recurring series. Fetches GET /api/charts/{seriesId} and renders one
 * editable `SeriesChartTile` (title/description editable, membership editable).
 * The seriesId is the stable `{sender_id}-{kind_id}-{currency|none}` id.
 */
import { onMounted, ref, watch } from 'vue'
import { useRoute, RouterLink } from 'vue-router'
import { fetchChart, type DocumentSeries } from '@/api/documents'
import SeriesChartTile from '@/components/SeriesChartTile.vue'

const route = useRoute()
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

    <div v-else-if="chart" class="max-w-2xl">
      <SeriesChartTile :series="chart" editable @changed="load" />
    </div>
  </div>
</template>
