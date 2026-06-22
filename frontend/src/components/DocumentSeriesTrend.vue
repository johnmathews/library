<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Line } from 'vue-chartjs'
import {
  Chart as ChartJS, CategoryScale, LinearScale, PointElement, LineElement, Tooltip,
} from 'chart.js'
import { fetchDocumentSeries, type DocumentSeries } from '@/api/documents'

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip)

const props = defineProps<{ documentId: number }>()
const series = ref<DocumentSeries | null>(null)

onMounted(async () => {
  try {
    const data = await fetchDocumentSeries(props.documentId)
    series.value = data.status === 'ok' ? data : null
  } catch {
    series.value = null
  }
})

const verdictText = computed<string>(() => {
  const s = series.value
  if (!s?.reference) return ''
  const pct = s.reference.vs_median_pct
  if (s.reference.verdict === 'typical') return 'about usual'
  return `${pct.replace('+', '').replace('-', '')} ${s.reference.verdict === 'higher' ? 'above' : 'below'} usual`
})

const trendText = computed<string>(() => (series.value?.trend ? `trend ${series.value.trend.direction}` : ''))

const chartData = computed(() => {
  const pts = series.value?.points ?? []
  return {
    labels: pts.map((p) => p.date),
    datasets: [
      {
        data: pts.map((p) => Number(p.amount)),
        borderColor: '#2563eb',
        pointBackgroundColor: pts.map((p, i) =>
          i === pts.length - 1 ? '#dc2626' : '#2563eb',
        ),
        tension: 0.2,
      },
    ],
  }
})

const chartOptions = {
  responsive: true,
  maintainAspectRatio: false,
  plugins: { legend: { display: false } },
}
</script>

<template>
  <section
    v-if="series"
    data-testid="series-trend"
    class="mt-4 rounded-lg border border-gray-200 p-4 dark:border-gray-700"
  >
    <h3 class="text-sm font-medium text-gray-700 dark:text-gray-300">
      {{ series.sender }} · {{ series.cadence }} series
    </h3>
    <p class="text-sm text-gray-600 dark:text-gray-400">
      <span v-if="verdictText">{{ verdictText }}</span>
      <span v-if="verdictText && trendText"> · </span>
      <span v-if="trendText">{{ trendText }}</span>
    </p>
    <div class="mt-3 h-40">
      <Line :data="chartData" :options="chartOptions" />
    </div>
  </section>
</template>
