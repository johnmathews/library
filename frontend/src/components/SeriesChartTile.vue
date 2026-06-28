<script setup lang="ts">
import { computed, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { Bar } from 'vue-chartjs'
import { Chart as ChartJS, CategoryScale, LinearScale, BarElement, Tooltip } from 'chart.js'
import {
  addSeriesMember,
  removeSeriesMember,
  listDocuments,
  type DocumentSeries,
  type DocumentListItem,
} from '@/api/documents'

ChartJS.register(CategoryScale, LinearScale, BarElement, Tooltip)

const props = defineProps<{
  series: DocumentSeries
  // Highlight the point for this document (e.g. the one being viewed). When
  // omitted the most recent point is highlighted.
  highlightDocumentId?: number
  // Show add/remove controls for "documents in this series" (W8). Only takes
  // effect when the series has a resolved identity (sender_id + kind_id).
  editable?: boolean
}>()

// Emitted after a successful add/remove so the parent can refetch the series.
const emit = defineEmits<{ changed: [] }>()

const points = computed(() => props.series.points ?? [])

// Membership can only be edited for a series with a concrete identity; the
// override store is keyed by (sender_id, kind_id, currency).
const canEdit = computed(
  () =>
    props.editable === true &&
    props.series.sender_id != null &&
    props.series.kind_id != null,
)

const busy = ref(false)
const showAdd = ref(false)
const query = ref('')
const results = ref<DocumentListItem[]>([])

async function onRemove(documentId: number): Promise<void> {
  if (!canEdit.value || busy.value) return
  busy.value = true
  try {
    await removeSeriesMember(
      props.series.sender_id!,
      props.series.kind_id!,
      documentId,
      props.series.currency,
    )
    emit('changed')
  } finally {
    busy.value = false
  }
}

async function onSearch(): Promise<void> {
  const q = query.value.trim()
  if (!q) {
    results.value = []
    return
  }
  const response = await listDocuments({ q, limit: 8 })
  results.value = response.items
}

async function onAdd(documentId: number): Promise<void> {
  if (!canEdit.value || busy.value) return
  busy.value = true
  try {
    await addSeriesMember(
      props.series.sender_id!,
      props.series.kind_id!,
      documentId,
      props.series.currency,
    )
    query.value = ''
    results.value = []
    showAdd.value = false
    emit('changed')
  } finally {
    busy.value = false
  }
}

function resultLabel(doc: DocumentListItem): string {
  return doc.title?.trim() ? doc.title : `Document #${doc.id}`
}

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
      <div class="flex items-center justify-between">
        <h4 class="text-xs font-medium uppercase tracking-wide text-gray-400 dark:text-gray-500">
          Documents in this series
        </h4>
        <button
          v-if="canEdit"
          type="button"
          data-testid="series-add-toggle"
          class="text-xs text-violet-600 hover:text-violet-700 dark:text-violet-400 dark:hover:text-violet-300"
          @click="showAdd = !showAdd"
        >
          {{ showAdd ? 'Close' : '+ Add document' }}
        </button>
      </div>

      <form
        v-if="canEdit && showAdd"
        data-testid="series-add"
        class="mt-2"
        @submit.prevent="onSearch"
      >
        <div class="flex gap-2">
          <input
            v-model="query"
            type="search"
            data-testid="series-add-search"
            placeholder="Search documents to add…"
            class="form-input flex-1 min-w-0 text-sm"
            @input="onSearch"
          />
        </div>
        <ul v-if="results.length" data-testid="series-add-results" class="mt-2 space-y-1">
          <li v-for="doc in results" :key="doc.id">
            <button
              type="button"
              data-testid="series-add-result"
              class="text-sm text-left text-violet-600 hover:text-violet-700 dark:text-violet-400 dark:hover:text-violet-300 hover:underline disabled:opacity-50"
              :disabled="busy"
              @click="onAdd(doc.id)"
            >
              + {{ resultLabel(doc) }}
            </button>
          </li>
        </ul>
      </form>

      <ul data-testid="series-citations" class="mt-2 flex flex-wrap gap-x-3 gap-y-1">
        <li
          v-for="point in points"
          :key="point.document_id"
          class="inline-flex items-center gap-1"
        >
          <RouterLink
            :to="`/documents/${point.document_id}`"
            data-testid="series-citation"
            class="text-sm text-violet-600 hover:text-violet-700 dark:text-violet-400 dark:hover:text-violet-300 hover:underline"
            :title="`${point.date} · ${point.amount}`"
          >
            {{ pointLabel(point) }}
            <span class="text-gray-400 dark:text-gray-500">· {{ point.amount }}</span>
          </RouterLink>
          <button
            v-if="canEdit"
            type="button"
            data-testid="series-remove"
            class="text-gray-400 hover:text-red-600 dark:hover:text-red-400 disabled:opacity-50"
            :disabled="busy"
            :aria-label="`Remove ${pointLabel(point)} from this series`"
            @click="onRemove(point.document_id)"
          >
            ×
          </button>
        </li>
      </ul>
    </div>
  </section>
</template>
