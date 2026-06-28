<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { Bar } from 'vue-chartjs'
import { Chart as ChartJS, CategoryScale, LinearScale, BarElement, Tooltip } from 'chart.js'
import type { DocumentSeries } from '@/api/documents'

ChartJS.register(CategoryScale, LinearScale, BarElement, Tooltip)

const props = defineProps<{
  series: DocumentSeries
  // Highlight the point for this document (e.g. the one being viewed). When
  // omitted the most recent point is highlighted.
  highlightDocumentId?: number
}>()

const points = computed(() => props.series.points ?? [])

const activeIdx = computed<number>(() => {
  const pts = points.value
  if (props.highlightDocumentId !== undefined) {
    const idx = pts.findIndex((p) => p.document_id === props.highlightDocumentId)
    if (idx !== -1) return idx
  }
  return pts.length - 1
})

const verdictText = computed<string>(() => {
  const ref = props.series.reference
  if (!ref) return ''
  if (ref.verdict === 'typical') return 'about usual'
  // vs_median_pct is always a signed string like "+30.0%" / "-5.2%"; drop the sign.
  const pct = ref.vs_median_pct.slice(1)
  return `${pct} ${ref.verdict === 'higher' ? 'above' : 'below'} usual`
})

const trendText = computed<string>(() =>
  props.series.trend ? `trend ${props.series.trend.direction}` : '',
)

const chartData = computed(() => {
  const pts = points.value
  const active = activeIdx.value
  // Bars, not a line: these are discrete recurring events (one document per
  // bar), not a continuous signal. The active bar is highlighted red.
  return {
    labels: pts.map((p) => p.date),
    datasets: [
      {
        data: pts.map((p) => Number(p.amount)),
        backgroundColor: pts.map((_, i) => (i === active ? '#dc2626' : '#2563eb')),
        borderRadius: 4,
        maxBarThickness: 48,
      },
    ],
  }
})

const chartOptions = {
  responsive: true,
  maintainAspectRatio: false,
  plugins: { legend: { display: false } },
}

function pointLabel(point: { title?: string | null; date: string }): string {
  return point.title?.trim() ? point.title : point.date
}
</script>

<template>
  <section
    data-testid="series-trend"
    class="bg-white dark:bg-gray-800 shadow-xs rounded-xl border border-gray-200 dark:border-gray-700/60 p-5"
  >
    <header>
      <h3
        class="text-sm font-semibold text-gray-800 dark:text-gray-100"
        data-testid="series-heading"
      >
        {{ series.sender }} · {{ series.cadence }} series
        <span class="text-gray-400 dark:text-gray-500 font-normal">
          ({{ series.count }} documents<span v-if="series.currency">, {{ series.currency }}</span
          >)
        </span>
      </h3>
      <p v-if="verdictText || trendText" class="mt-0.5 text-sm text-gray-600 dark:text-gray-400">
        <span v-if="verdictText">{{ verdictText }}</span>
        <span v-if="verdictText && trendText"> · </span>
        <span v-if="trendText">{{ trendText }}</span>
      </p>
    </header>

    <p
      v-if="series.description"
      data-testid="series-description"
      class="mt-3 text-sm text-gray-700 dark:text-gray-300"
    >
      {{ series.description }}
    </p>

    <div class="mt-3 h-40">
      <Bar :data="chartData" :options="chartOptions" />
    </div>

    <div v-if="points.length" class="mt-4">
      <h4 class="text-xs font-medium uppercase tracking-wide text-gray-400 dark:text-gray-500">
        Documents in this series
      </h4>
      <ul data-testid="series-citations" class="mt-2 flex flex-wrap gap-x-3 gap-y-1">
        <li v-for="point in points" :key="point.document_id">
          <RouterLink
            :to="`/documents/${point.document_id}`"
            data-testid="series-citation"
            class="text-sm text-violet-600 hover:text-violet-700 dark:text-violet-400 dark:hover:text-violet-300 hover:underline"
            :title="`${point.date} · ${point.amount}`"
          >
            {{ pointLabel(point) }}
            <span class="text-gray-400 dark:text-gray-500">· {{ point.amount }}</span>
          </RouterLink>
        </li>
      </ul>
    </div>
  </section>
</template>
