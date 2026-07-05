<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { Bar } from 'vue-chartjs'
import { Chart as ChartJS, TimeScale, LinearScale, BarElement, Tooltip } from 'chart.js'
// Date adapter for chart.js's time scale (registered as a side effect).
import 'chartjs-adapter-date-fns'
import {
  addSeriesMember,
  removeSeriesMember,
  listDocuments,
  seriesId,
  authoredSeriesId,
  updateSeriesMeta,
  updateAuthoredSeries,
  deleteAuthoredSeries,
  addAuthoredMember,
  removeAuthoredMember,
  fetchAuthoredSuggestions,
  acceptAuthoredSuggestion,
  dismissAuthoredSuggestion,
  fetchAuthoredOddOnesOut,
  type DocumentSeries,
  type DocumentListItem,
  type SeriesSuggestion,
  type SeriesOddOneOut,
} from '@/api/documents'
import {
  groupSeriesPoints,
  type ChartGrouping,
  type GroupedPoint,
} from '@/composables/useChartsGrouping'

ChartJS.register(TimeScale, LinearScale, BarElement, Tooltip)

const props = defineProps<{
  series: DocumentSeries
  // Highlight the point for this document (e.g. the one being viewed). When
  // omitted the most recent point is highlighted.
  highlightDocumentId?: number
  // Show add/remove controls for "documents in this series" (W8) plus the
  // inline title/description editor (W12). Only takes effect when the series
  // has a resolved identity (sender_id + kind_id).
  editable?: boolean
  // Render a deep link to this series' own single-chart page (/charts/:id).
  // Off on the single-chart view itself (it would link to itself).
  detailLink?: boolean
  // Shared time-axis window (W4). When set, the chart's x-axis is clamped to
  // [axisMin, axisMax] (ISO yyyy-mm-dd) so every tile on /charts shares the
  // same window. Null/omitted leaves the axis auto-fitted to this series' data.
  axisMin?: string | null
  axisMax?: string | null
  // Time-bucket grouping. When set to something other than "none", the
  // per-document bars are replaced by one bar per calendar period whose height
  // is the SUM of the documents that fall in it. Display-only; membership is
  // unchanged.
  grouping?: ChartGrouping
  // Render the chart taller (the single-chart / full-screen view). The grid
  // tiles keep the compact default.
  size?: 'default' | 'large'
}>()

// `changed` fires after an add/remove so the parent can refetch the series;
// `deleted` fires after the whole (authored) series is removed so the parent
// can drop the tile / leave the single-chart page (W4).
const emit = defineEmits<{ changed: []; deleted: [] }>()

const points = computed(() => props.series.points ?? [])

// An authored (user-curated) series carries its own id; it has no
// sender/kind identity and edits its own row rather than an override.
const isAuthored = computed(() => props.series.authored_id != null)

// Membership can be edited for an emergent series with a concrete identity
// (the override store is keyed by (sender_id, kind_id, currency)) or for any
// authored series (membership is explicit rows).
const canEdit = computed(
  () =>
    props.editable === true &&
    (isAuthored.value ||
      (props.series.sender_id != null && props.series.kind_id != null)),
)

// Stable id for this series + its single-chart deep link.
const id = computed(() =>
  isAuthored.value ? authoredSeriesId(props.series.authored_id!) : seriesId(props.series),
)
const detailHref = computed(() => `/charts/${id.value}`)

// Whole-tile "click to open the chart" (W3): the heading and the chart area
// navigate to the single-chart page when this tile links to a detail view.
// Only active where `detailLink` is set — never on the detail page itself
// (would self-link) or on the document-trend embed.
const router = useRouter()
function openDetail(): void {
  if (props.detailLink) router?.push(detailHref.value)
}

// Heading prefers a user title override (W12) over the derived label.
const headingMain = computed<string>(() =>
  props.series.title?.trim()
    ? props.series.title
    : `${props.series.sender} · ${props.series.cadence} series`,
)

// Handle to the underlying vue-chartjs <Bar> so the parent can grab the canvas
// for image/PDF export (W6). vue-chartjs exposes the Chart.js instance as
// `.chart`; its `.canvas` is the rendered element.
const barRef = ref<{ chart?: { canvas: HTMLCanvasElement } } | null>(null)
function getChartCanvas(): HTMLCanvasElement | null {
  return barRef.value?.chart?.canvas ?? null
}
defineExpose({ getChartCanvas })

const busy = ref(false)
const showAdd = ref(false)
const query = ref('')
const results = ref<DocumentListItem[]>([])

// Inline title/description editing (W12). Open the form prefilled from the
// current (possibly overridden) values; saving PUTs the meta override.
const editingMeta = ref(false)
const metaTitle = ref('')
const metaDescription = ref('')

function onEditMeta(): void {
  metaTitle.value = props.series.title ?? ''
  metaDescription.value = props.series.description ?? ''
  editingMeta.value = true
}

function onCancelMeta(): void {
  editingMeta.value = false
}

async function onSaveMeta(): Promise<void> {
  if (!canEdit.value || busy.value) return
  busy.value = true
  try {
    if (isAuthored.value) {
      // The authored series' title IS its name (required). A blank title leaves
      // the name unchanged; the description clears with null.
      const name = metaTitle.value.trim()
      await updateAuthoredSeries(props.series.authored_id!, {
        ...(name ? { name } : {}),
        description: metaDescription.value.trim() || null,
      })
    } else {
      // Empty inputs clear the override (send null), so a user can revert to the
      // derived heading / cached description.
      await updateSeriesMeta(id.value, {
        title: metaTitle.value.trim() || null,
        description: metaDescription.value.trim() || null,
      })
    }
    editingMeta.value = false
    emit('changed')
  } finally {
    busy.value = false
  }
}

// --- Delete the whole series (authored only) -------------------------------
//
// Only authored (user-curated) series can be deleted — emergent series are
// computed from the documents themselves, so there is no row to remove.
// Deletion is a two-step inline confirm (no blocking native dialog).
const canDelete = computed(() => canEdit.value && isAuthored.value)
const confirmingDelete = ref(false)
const deleteError = ref<string | null>(null)

function onDeleteClick(): void {
  deleteError.value = null
  confirmingDelete.value = true
}

function onCancelDelete(): void {
  confirmingDelete.value = false
}

async function onConfirmDelete(): Promise<void> {
  if (!canDelete.value || busy.value) return
  busy.value = true
  deleteError.value = null
  try {
    await deleteAuthoredSeries(props.series.authored_id!)
    confirmingDelete.value = false
    emit('deleted')
  } catch {
    deleteError.value = 'Could not delete this chart. Try again.'
  } finally {
    busy.value = false
  }
}

// "Documents in this series" is collapsed by default — the list can be long and
// dominates the card otherwise (W8). The toggle reveals it.
const showDocs = ref(false)

// The most recently removed document, kept so the user can undo. The override
// toggle is self-reversing: re-adding a removed doc clears the exclude (W8).
const lastRemoved = ref<{ id: number; label: string } | null>(null)

async function onRemove(documentId: number, label: string): Promise<void> {
  if (!canEdit.value || busy.value) return
  busy.value = true
  try {
    if (isAuthored.value) {
      await removeAuthoredMember(props.series.authored_id!, documentId)
    } else {
      await removeSeriesMember(
        props.series.sender_id!,
        props.series.kind_id!,
        documentId,
        props.series.currency,
      )
    }
    lastRemoved.value = { id: documentId, label }
    emit('changed')
  } finally {
    busy.value = false
  }
}

async function onUndoRemove(): Promise<void> {
  const removed = lastRemoved.value
  if (!removed || !canEdit.value || busy.value) return
  busy.value = true
  try {
    if (isAuthored.value) {
      await addAuthoredMember(props.series.authored_id!, removed.id)
    } else {
      await addSeriesMember(
        props.series.sender_id!,
        props.series.kind_id!,
        removed.id,
        props.series.currency,
      )
    }
    lastRemoved.value = null
    emit('changed')
  } finally {
    busy.value = false
  }
}

// --- Smart features: suggestions & odd-ones-out (authored series only) -------
//
// Counts arrive with the series body (suggestion_count / odd_one_out_count), so
// the badges render without a fetch. The lists themselves are loaded lazily on
// expand — odd-ones-out in particular triggers a per-member LLM call server-side,
// so we only ask for it when the user opens the panel.

const suggestionCount = computed<number>(() => props.series.suggestion_count ?? 0)
const oddOneOutCount = computed<number>(() => props.series.odd_one_out_count ?? 0)

// Only authored series that the user can edit expose these affordances.
const canSuggest = computed(() => canEdit.value && isAuthored.value)

const showSuggestions = ref(false)
const suggestions = ref<SeriesSuggestion[]>([])
const loadingSuggestions = ref(false)

async function loadSuggestions(): Promise<void> {
  if (!isAuthored.value) return
  loadingSuggestions.value = true
  try {
    const response = await fetchAuthoredSuggestions(props.series.authored_id!)
    suggestions.value = response.suggestions
  } finally {
    loadingSuggestions.value = false
  }
}

async function onToggleSuggestions(): Promise<void> {
  showSuggestions.value = !showSuggestions.value
  if (showSuggestions.value) await loadSuggestions()
}

async function onAcceptSuggestion(documentId: number): Promise<void> {
  if (!canSuggest.value || busy.value) return
  busy.value = true
  try {
    await acceptAuthoredSuggestion(props.series.authored_id!, documentId)
    suggestions.value = suggestions.value.filter((s) => s.id !== documentId)
    emit('changed')
  } finally {
    busy.value = false
  }
}

async function onDismissSuggestion(documentId: number): Promise<void> {
  if (!canSuggest.value || busy.value) return
  busy.value = true
  try {
    await dismissAuthoredSuggestion(props.series.authored_id!, documentId)
    suggestions.value = suggestions.value.filter((s) => s.id !== documentId)
    emit('changed')
  } finally {
    busy.value = false
  }
}

const showOddOnesOut = ref(false)
const oddOnesOut = ref<SeriesOddOneOut[]>([])
const loadingOddOnesOut = ref(false)

async function loadOddOnesOut(): Promise<void> {
  if (!isAuthored.value) return
  loadingOddOnesOut.value = true
  try {
    const response = await fetchAuthoredOddOnesOut(props.series.authored_id!)
    oddOnesOut.value = response.members
  } finally {
    loadingOddOnesOut.value = false
  }
}

async function onToggleOddOnesOut(): Promise<void> {
  showOddOnesOut.value = !showOddOnesOut.value
  if (showOddOnesOut.value && oddOnesOut.value.length === 0) await loadOddOnesOut()
}

function suggestionLabel(s: SeriesSuggestion): string {
  return s.title?.trim() ? s.title : `Document #${s.id}`
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
    if (isAuthored.value) {
      await addAuthoredMember(props.series.authored_id!, documentId)
    } else {
      await addSeriesMember(
        props.series.sender_id!,
        props.series.kind_id!,
        documentId,
        props.series.currency,
      )
    }
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

// True when a real time-bucket grouping is in effect.
const isGrouped = computed<boolean>(() => props.grouping != null && props.grouping !== 'none')

// Summed buckets for grouped mode (empty otherwise). Kept separate so the
// tooltip can report the per-bucket document count and per-document breakdown.
// Each point carries its label so the tooltip can name the contributing docs.
const groupedBuckets = computed<GroupedPoint[]>(() =>
  isGrouped.value
    ? groupSeriesPoints(
        points.value.map((p) => ({
          date: p.date,
          amount: p.amount,
          label: pointLabel(p),
          document_id: p.document_id,
        })),
        props.grouping as Exclude<ChartGrouping, 'none'>,
      )
    : [],
)

const chartData = computed(() => {
  // Grouped: one bar per calendar period, height = SUM of the period's
  // documents. No single "active" document maps to a bucket, so bars are
  // coloured uniformly.
  if (isGrouped.value) {
    return {
      datasets: [
        {
          data: groupedBuckets.value.map((b) => ({ x: b.x, y: b.y })),
          backgroundColor: '#2563eb',
          borderRadius: 4,
          maxBarThickness: 32,
        },
      ],
    }
  }
  const pts = points.value
  const active = activeIdx.value
  // Bars, not a line: these are discrete recurring events (one document per
  // bar), not a continuous signal. The active bar is highlighted red. Points
  // carry {x: date, y: amount} so the time axis spaces them by real elapsed
  // time, not evenly (W9).
  return {
    datasets: [
      {
        data: pts.map((p) => ({ x: p.date, y: Number(p.amount) })),
        backgroundColor: pts.map((_, i) => (i === active ? '#dc2626' : '#2563eb')),
        borderRadius: 4,
        maxBarThickness: 32,
      },
    ],
  }
})

// Coarser tick unit when grouped so period bars label cleanly. The literal
// union matches Chart.js's TimeUnit so the options object type-checks.
const timeUnit = computed<'week' | 'month' | 'quarter' | 'year' | undefined>(() => {
  switch (props.grouping) {
    case 'week':
      return 'week'
    case 'month':
      return 'month'
    case 'quarter':
      return 'quarter'
    case 'year':
      return 'year'
    default:
      return undefined
  }
})

// Format an amount for the tooltip, appending the series currency when known.
function formatTooltipAmount(amount: number): string {
  const num = amount.toLocaleString(undefined, { maximumFractionDigits: 2 })
  return props.series.currency ? `${num} ${props.series.currency}` : num
}

// Grouped tooltip, first line: the bar's total + how many documents fed it.
function groupedTooltipLabel(ctx: { dataIndex: number; parsed: { y: number | null } }): string {
  const bucket = groupedBuckets.value[ctx.dataIndex]
  const n = bucket?.count ?? 0
  return `Total ${formatTooltipAmount(ctx.parsed.y ?? 0)} · ${n} ${n === 1 ? 'document' : 'documents'}`
}

// Grouped tooltip, following lines: each contributing document's amount on its
// own row (label: amount). Capped so a busy bucket doesn't produce a giant
// tooltip — the remainder is summarised as "+N more".
const TOOLTIP_ITEM_CAP = 12
function groupedTooltipAfterBody(items: { dataIndex: number }[]): string[] {
  const bucket = groupedBuckets.value[items[0]?.dataIndex ?? -1]
  if (!bucket) return []
  const rows = bucket.items
    .slice(0, TOOLTIP_ITEM_CAP)
    .map((it) => `${it.label}: ${formatTooltipAmount(it.amount)}`)
  if (bucket.items.length > TOOLTIP_ITEM_CAP) {
    rows.push(`+${bucket.items.length - TOOLTIP_ITEM_CAP} more`)
  }
  return rows
}

// --- Ungrouped: click a bar to open its document -----------------------------
//
// In ungrouped mode each bar is one document, so a bar click navigates to that
// document. Chart.js hands us the active element(s); its `.index` is the
// dataIndex, which maps 1:1 to points.value. We stop propagation so a bar click
// on a whole-tile "open chart" tile (detailLink) opens the DOCUMENT, not the
// chart page. Grouped mode has no per-document bar, so this is ungrouped-only.
interface ActiveBarElement {
  index: number
}
interface ChartEvent {
  native?: Event | null
}
function onBarClick(event: ChartEvent, elements: ActiveBarElement[]): void {
  if (!elements.length) return
  const documentId = points.value[elements[0]!.index]?.document_id
  if (documentId == null) return
  // Prevent the whole-tile openDetail handler (bubbling) from also firing.
  event.native?.stopPropagation?.()
  router?.push(`/documents/${documentId}`)
}

// Pointer cursor while over a clickable bar (ungrouped only).
function onBarHover(event: ChartEvent, elements: ActiveBarElement[]): void {
  const target = event.native?.target as HTMLElement | undefined
  if (target?.style) target.style.cursor = elements.length ? 'pointer' : 'default'
}

// --- Grouped: sticky HTML tooltip with clickable document rows ----------------
//
// A canvas tooltip can't hold clickable links, so in grouped mode we disable
// Chart.js's built-in tooltip and render our own absolutely-positioned HTML
// element (driven by Chart.js's `external` tooltip handler). It lists each
// source document in the bucket as a link and stays open while the pointer is
// over it — long enough to click a row.
interface TooltipModel {
  opacity: number
  caretX: number
  caretY: number
  dataPoints?: { dataIndex: number }[]
}
const tooltipVisible = ref(false)
const tooltipIndex = ref(-1)
const tooltipPos = ref<{ x: number; y: number }>({ x: 0, y: 0 })
let hideTimer: ReturnType<typeof setTimeout> | null = null

function cancelHideTooltip(): void {
  if (hideTimer !== null) {
    clearTimeout(hideTimer)
    hideTimer = null
  }
}
// Delay the hide so the pointer can travel from the bar onto the tooltip; a
// mouseenter on the tooltip cancels it (see the template).
function scheduleHideTooltip(): void {
  cancelHideTooltip()
  hideTimer = setTimeout(() => {
    tooltipVisible.value = false
    hideTimer = null
  }, 200)
}
function hideTooltipNow(): void {
  cancelHideTooltip()
  tooltipVisible.value = false
}

function externalTooltipHandler(context: { chart: unknown; tooltip: TooltipModel }): void {
  const model = context.tooltip
  if (!model || model.opacity === 0) {
    scheduleHideTooltip()
    return
  }
  const dataIndex = model.dataPoints?.[0]?.dataIndex
  if (dataIndex == null || !groupedBuckets.value[dataIndex]) return
  cancelHideTooltip()
  tooltipIndex.value = dataIndex
  tooltipPos.value = { x: model.caretX, y: model.caretY }
  tooltipVisible.value = true
}

// The bucket currently under the tooltip, plus its formatted header line.
const tooltipBucket = computed<GroupedPoint | null>(() =>
  tooltipVisible.value ? (groupedBuckets.value[tooltipIndex.value] ?? null) : null,
)
const tooltipHeader = computed<string>(() => {
  const bucket = tooltipBucket.value
  if (!bucket) return ''
  const n = bucket.count
  return `Total ${formatTooltipAmount(bucket.y)} · ${n} ${n === 1 ? 'document' : 'documents'}`
})

// Never leak the grouped overlay into ungrouped mode.
watch(isGrouped, (grouped) => {
  if (!grouped) hideTooltipNow()
})
onBeforeUnmount(cancelHideTooltip)

const chartOptions = computed(() => ({
  responsive: true,
  maintainAspectRatio: false,
  // Per-bar click/hover only makes sense ungrouped (one bar = one document).
  ...(isGrouped.value ? {} : { onClick: onBarClick, onHover: onBarHover }),
  plugins: {
    legend: { display: false },
    tooltip: isGrouped.value
      ? {
          // Suppress the canvas tooltip; the HTML overlay replaces it so rows
          // can be links. Callbacks stay so the model still carries the data.
          enabled: false,
          external: externalTooltipHandler,
          callbacks: { label: groupedTooltipLabel, afterBody: groupedTooltipAfterBody },
        }
      : { callbacks: {} },
  },
  scales: {
    // Temporal x-axis: the gap between two events reflects the real time
    // between them (3 months apart sit ~3× farther than 1 month apart). When a
    // shared window is supplied (W4), clamp min/max so every tile lines up.
    x: {
      type: 'time' as const,
      time: { tooltipFormat: 'yyyy-MM-dd', ...(timeUnit.value ? { unit: timeUnit.value } : {}) },
      grid: { display: false },
      ticks: { maxRotation: 45, minRotation: 0, autoSkip: true, font: { size: 10 } },
      ...(props.axisMin ? { min: props.axisMin } : {}),
      ...(props.axisMax ? { max: props.axisMax } : {}),
    },
    y: { beginAtZero: true, ticks: { font: { size: 10 } } },
  },
}))

function pointLabel(point: { title?: string | null; date: string }): string {
  return point.title?.trim() ? point.title : point.date
}
</script>

<template>
  <section
    data-testid="series-trend"
    class="card p-5"
  >
    <header>
      <div class="flex items-start justify-between gap-2">
        <h3
          class="text-sm font-semibold text-gray-800 dark:text-gray-100"
          data-testid="series-heading"
        >
          <RouterLink
            v-if="detailLink"
            :to="detailHref"
            data-testid="series-heading-link"
            class="hover:underline"
          >
            {{ headingMain }}
          </RouterLink>
          <template v-else>{{ headingMain }}</template>
        </h3>
        <div class="flex shrink-0 items-center gap-2">
          <button
            v-if="canEdit && !editingMeta"
            type="button"
            data-testid="series-meta-edit"
            class="text-xs text-violet-600 hover:text-violet-700 dark:text-violet-400 dark:hover:text-violet-300"
            @click="onEditMeta"
          >
            Edit
          </button>
          <button
            v-if="canDelete && !editingMeta && !confirmingDelete"
            type="button"
            data-testid="series-delete"
            class="text-xs text-gray-500 hover:text-red-600 dark:text-gray-400 dark:hover:text-red-400"
            @click="onDeleteClick"
          >
            Delete
          </button>
          <RouterLink
            v-if="detailLink"
            :to="detailHref"
            data-testid="series-detail-link"
            class="text-xs text-violet-600 hover:text-violet-700 dark:text-violet-400 dark:hover:text-violet-300 hover:underline"
          >
            Open chart
          </RouterLink>
        </div>
      </div>
    </header>

    <!-- Inline delete confirmation (W4). Two-step, no blocking dialog. -->
    <div
      v-if="confirmingDelete"
      data-testid="series-delete-confirm"
      class="mt-3 rounded-md border border-red-300 dark:border-red-500/30 bg-red-50 dark:bg-red-500/10 px-3 py-2"
    >
      <p class="text-sm text-red-800 dark:text-red-300">
        Delete this chart? The documents in it are not affected.
      </p>
      <p
        v-if="deleteError"
        data-testid="series-delete-error"
        role="alert"
        class="mt-1 text-xs text-red-600 dark:text-red-400"
      >
        {{ deleteError }}
      </p>
      <div class="mt-2 flex justify-end gap-2">
        <button
          type="button"
          data-testid="series-delete-cancel"
          class="text-xs text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 disabled:opacity-50"
          :disabled="busy"
          @click="onCancelDelete"
        >
          Cancel
        </button>
        <button
          type="button"
          data-testid="series-delete-confirm-button"
          class="text-xs font-medium text-red-600 hover:text-red-700 dark:text-red-400 dark:hover:text-red-300 disabled:opacity-50"
          :disabled="busy"
          @click="onConfirmDelete"
        >
          Delete chart
        </button>
      </div>
    </div>

    <!-- Inline title/description editor (W12). -->
    <form
      v-if="editingMeta"
      data-testid="series-meta-form"
      class="mt-3 space-y-2"
      @submit.prevent="onSaveMeta"
    >
      <input
        v-model="metaTitle"
        type="text"
        data-testid="series-title-input"
        placeholder="Title (leave blank to use the default)"
        class="form-input w-full text-sm"
      />
      <textarea
        v-model="metaDescription"
        data-testid="series-description-input"
        rows="3"
        placeholder="Description"
        class="form-textarea w-full text-sm"
      ></textarea>
      <div class="flex justify-end gap-2">
        <button
          type="button"
          data-testid="series-meta-cancel"
          class="text-xs text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
          :disabled="busy"
          @click="onCancelMeta"
        >
          Cancel
        </button>
        <button
          type="submit"
          data-testid="series-meta-save"
          class="text-xs font-medium text-violet-600 hover:text-violet-700 dark:text-violet-400 dark:hover:text-violet-300 disabled:opacity-50"
          :disabled="busy"
        >
          Save
        </button>
      </div>
    </form>

    <p
      v-else-if="series.description"
      data-testid="series-description"
      class="mt-2 text-sm text-gray-700 dark:text-gray-300"
    >
      {{ series.description }}
    </p>

    <!-- Metadata, below the title + description: the count/currency and the
         trend analysis each on their own line for quick scanning. -->
    <div class="mt-3 space-y-0.5 text-sm" data-testid="series-meta">
      <p class="text-gray-500 dark:text-gray-400" data-testid="series-meta-count">
        {{ series.count }} {{ series.count === 1 ? 'document' : 'documents'
        }}<span v-if="series.currency"> · {{ series.currency }}</span>
      </p>
      <p
        v-if="verdictText || trendText"
        class="text-gray-600 dark:text-gray-400"
        data-testid="series-meta-analysis"
      >
        <span v-if="verdictText">{{ verdictText }}</span>
        <span v-if="verdictText && trendText"> · </span>
        <span v-if="trendText">{{ trendText }}</span>
      </p>
    </div>

    <div
      class="relative mt-3"
      :class="[size === 'large' ? 'h-[28rem]' : 'h-40', detailLink ? 'cursor-pointer' : '']"
      :role="detailLink ? 'link' : undefined"
      :tabindex="detailLink ? 0 : undefined"
      :aria-label="detailLink ? `Open ${headingMain} chart` : undefined"
      data-testid="series-chart-area"
      @click="openDetail"
      @keydown.enter="openDetail"
    >
      <Bar ref="barRef" :data="chartData" :options="chartOptions" />

      <!-- Grouped mode: sticky HTML tooltip (a canvas tooltip can't hold links).
           Lists the bucket's source documents as links; stays open while the
           pointer is over it. `@click.stop` keeps a row click from bubbling to
           the whole-tile openDetail handler. -->
      <div
        v-if="isGrouped && tooltipVisible && tooltipBucket"
        data-testid="chart-tooltip"
        class="pointer-events-auto absolute z-10 max-w-[16rem] -translate-x-1/2 rounded-md border border-gray-200 bg-white p-2 text-xs shadow-lg dark:border-gray-700 dark:bg-gray-800"
        :style="{ left: `${tooltipPos.x}px`, top: `${tooltipPos.y}px` }"
        @mouseenter="cancelHideTooltip"
        @mouseleave="hideTooltipNow"
        @click.stop
      >
        <p
          data-testid="chart-tooltip-header"
          class="mb-1 font-semibold text-gray-800 dark:text-gray-100"
        >
          {{ tooltipHeader }}
        </p>
        <ul class="space-y-0.5">
          <li
            v-for="(item, i) in tooltipBucket.items"
            :key="item.document_id ?? i"
            class="flex items-baseline justify-between gap-2"
          >
            <RouterLink
              v-if="item.document_id != null"
              :to="`/documents/${item.document_id}`"
              data-testid="chart-tooltip-doc-link"
              class="min-w-0 truncate text-violet-600 hover:text-violet-700 dark:text-violet-400 dark:hover:text-violet-300 hover:underline"
              :title="item.label"
            >
              {{ item.label }}
            </RouterLink>
            <span v-else class="min-w-0 truncate text-gray-700 dark:text-gray-300">{{
              item.label
            }}</span>
            <span class="shrink-0 tabular-nums text-gray-500 dark:text-gray-400">{{
              formatTooltipAmount(item.amount)
            }}</span>
          </li>
        </ul>
      </div>
    </div>

    <!-- Smart features (authored series): propose-for-review suggestions and
         odd-one-out warnings. Counts come with the series body; lists load on
         expand. -->
    <div v-if="canSuggest && (suggestionCount > 0 || oddOneOutCount > 0)" class="mt-3 space-y-2">
      <!-- Suggestions: documents that match the signature, awaiting review. -->
      <div
        v-if="suggestionCount > 0"
        data-testid="series-suggestions"
        class="rounded-md border border-violet-200 dark:border-violet-500/30 bg-violet-50 dark:bg-violet-500/10"
      >
        <button
          type="button"
          data-testid="series-suggestions-toggle"
          class="flex w-full items-center justify-between gap-2 px-3 py-2 text-sm font-medium text-violet-700 dark:text-violet-300"
          :aria-expanded="showSuggestions"
          @click="onToggleSuggestions"
        >
          <span>
            {{ suggestionCount }}
            {{ suggestionCount === 1 ? 'document looks' : 'documents look' }} like they belong —
            review
          </span>
          <svg
            class="h-4 w-4 shrink-0 transition-transform"
            :class="{ 'rotate-180': showSuggestions }"
            viewBox="0 0 20 20"
            fill="currentColor"
            aria-hidden="true"
          >
            <path
              fill-rule="evenodd"
              d="M5.23 7.21a.75.75 0 0 1 1.06.02L10 11.17l3.71-3.94a.75.75 0 1 1 1.08 1.04l-4.25 4.5a.75.75 0 0 1-1.08 0l-4.25-4.5a.75.75 0 0 1 .02-1.06Z"
              clip-rule="evenodd"
            />
          </svg>
        </button>
        <div v-show="showSuggestions" class="px-3 pb-2">
          <p
            v-if="loadingSuggestions"
            data-testid="series-suggestions-loading"
            class="py-1 text-xs text-gray-500 dark:text-gray-400"
          >
            Finding matches…
          </p>
          <ul v-else class="divide-y divide-violet-100 dark:divide-violet-500/20">
            <li
              v-for="s in suggestions"
              :key="s.id"
              data-testid="series-suggestion"
              class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 py-1.5"
            >
              <RouterLink
                :to="`/documents/${s.id}`"
                class="min-w-0 truncate text-sm text-gray-700 dark:text-gray-200 hover:underline"
                :title="suggestionLabel(s)"
              >
                {{ suggestionLabel(s) }}
                <span class="text-xs text-gray-400 dark:text-gray-500">
                  · {{ s.document_date ?? '—' }} · {{ s.amount }}{{ s.currency ? ` ${s.currency}` : '' }}
                </span>
              </RouterLink>
              <span class="flex shrink-0 items-center gap-2">
                <button
                  type="button"
                  data-testid="series-suggestion-accept"
                  class="text-xs font-medium text-violet-600 hover:text-violet-700 dark:text-violet-400 dark:hover:text-violet-300 disabled:opacity-50"
                  :disabled="busy"
                  @click="onAcceptSuggestion(s.id)"
                >
                  Add
                </button>
                <button
                  type="button"
                  data-testid="series-suggestion-dismiss"
                  class="text-xs text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 disabled:opacity-50"
                  :disabled="busy"
                  @click="onDismissSuggestion(s.id)"
                >
                  Dismiss
                </button>
              </span>
            </li>
          </ul>
        </div>
      </div>

      <!-- Odd-ones-out: current members that break the signature. Loaded lazily
           because the reason sentence is generated server-side by the LLM. -->
      <div
        v-if="oddOneOutCount > 0"
        data-testid="series-odd-ones-out"
        class="rounded-md border border-amber-300 dark:border-amber-500/30 bg-amber-50 dark:bg-amber-500/10"
      >
        <button
          type="button"
          data-testid="series-odd-toggle"
          class="flex w-full items-center justify-between gap-2 px-3 py-2 text-sm font-medium text-amber-800 dark:text-amber-300"
          :aria-expanded="showOddOnesOut"
          @click="onToggleOddOnesOut"
        >
          <span>
            {{ oddOneOutCount }}
            {{ oddOneOutCount === 1 ? 'member looks' : 'members look' }} unlike the rest
          </span>
          <svg
            class="h-4 w-4 shrink-0 transition-transform"
            :class="{ 'rotate-180': showOddOnesOut }"
            viewBox="0 0 20 20"
            fill="currentColor"
            aria-hidden="true"
          >
            <path
              fill-rule="evenodd"
              d="M5.23 7.21a.75.75 0 0 1 1.06.02L10 11.17l3.71-3.94a.75.75 0 1 1 1.08 1.04l-4.25 4.5a.75.75 0 0 1-1.08 0l-4.25-4.5a.75.75 0 0 1 .02-1.06Z"
              clip-rule="evenodd"
            />
          </svg>
        </button>
        <div v-show="showOddOnesOut" class="px-3 pb-2">
          <p
            v-if="loadingOddOnesOut"
            data-testid="series-odd-loading"
            class="py-1 text-xs text-gray-500 dark:text-gray-400"
          >
            Checking the collection…
          </p>
          <ul v-else class="divide-y divide-amber-100 dark:divide-amber-500/20">
            <li
              v-for="m in oddOnesOut"
              :key="m.id"
              data-testid="series-odd-member"
              class="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-x-3 py-1.5"
            >
              <div class="min-w-0">
                <RouterLink
                  :to="`/documents/${m.id}`"
                  class="block min-w-0 truncate text-sm text-gray-700 dark:text-gray-200 hover:underline"
                  :title="suggestionLabel(m)"
                >
                  {{ suggestionLabel(m) }}
                </RouterLink>
                <p class="text-xs text-amber-700 dark:text-amber-300/90">
                  {{ m.reason ?? `Different ${m.axis} from the rest of the series.` }}
                </p>
              </div>
              <button
                v-if="canEdit"
                type="button"
                data-testid="series-odd-remove"
                class="shrink-0 text-xs text-gray-500 hover:text-red-600 dark:text-gray-400 dark:hover:text-red-400 disabled:opacity-50"
                :disabled="busy"
                @click="onRemove(m.id, suggestionLabel(m))"
              >
                Remove
              </button>
            </li>
          </ul>
        </div>
      </div>
    </div>

    <div v-if="points.length" class="mt-4">
      <!-- Collapsed by default: a toggle row showing the count. Expanding
           reveals the add control and the columnar document list. -->
      <button
        type="button"
        data-testid="series-docs-toggle"
        class="flex w-full items-center justify-between text-xs font-medium uppercase tracking-wide text-gray-400 hover:text-gray-600 dark:text-gray-500 dark:hover:text-gray-300"
        :aria-expanded="showDocs"
        @click="showDocs = !showDocs"
      >
        <span>Documents in this series ({{ points.length }})</span>
        <svg
          class="h-4 w-4 transition-transform"
          :class="{ 'rotate-180': showDocs }"
          viewBox="0 0 20 20"
          fill="currentColor"
          aria-hidden="true"
        >
          <path
            fill-rule="evenodd"
            d="M5.23 7.21a.75.75 0 0 1 1.06.02L10 11.17l3.71-3.94a.75.75 0 1 1 1.08 1.04l-4.25 4.5a.75.75 0 0 1-1.08 0l-4.25-4.5a.75.75 0 0 1 .02-1.06Z"
            clip-rule="evenodd"
          />
        </svg>
      </button>

      <div v-show="showDocs" data-testid="series-docs" class="mt-2">
        <div v-if="canEdit" class="flex justify-end">
          <button
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

        <!-- Undo banner: re-adds the last-removed document (the override toggle
             is self-reversing). -->
        <div
          v-if="canEdit && lastRemoved"
          data-testid="series-undo"
          class="mt-2 flex items-center justify-between gap-2 rounded-md bg-gray-50 dark:bg-gray-700/40 px-3 py-1.5 text-xs text-gray-600 dark:text-gray-300"
        >
          <span class="min-w-0 truncate">Removed “{{ lastRemoved.label }}”.</span>
          <button
            type="button"
            data-testid="series-undo-button"
            class="shrink-0 font-medium text-violet-600 hover:text-violet-700 dark:text-violet-400 dark:hover:text-violet-300 disabled:opacity-50"
            :disabled="busy"
            @click="onUndoRemove"
          >
            Undo
          </button>
        </div>

        <!-- One document per row, aligned in columns: title | date | amount. -->
        <ul data-testid="series-citations" class="mt-2 divide-y divide-gray-100 dark:divide-gray-700/60">
          <li
            v-for="point in points"
            :key="point.document_id"
            class="grid grid-cols-[minmax(0,1fr)_auto_auto_auto] items-baseline gap-x-3 py-1"
          >
            <RouterLink
              :to="`/documents/${point.document_id}`"
              data-testid="series-citation"
              class="min-w-0 truncate text-sm text-violet-600 hover:text-violet-700 dark:text-violet-400 dark:hover:text-violet-300 hover:underline"
              :title="pointLabel(point)"
            >
              {{ pointLabel(point) }}
            </RouterLink>
            <span class="text-xs tabular-nums text-gray-400 dark:text-gray-500">{{ point.date }}</span>
            <span class="text-sm tabular-nums text-right text-gray-700 dark:text-gray-300">{{ point.amount }}</span>
            <button
              v-if="canEdit"
              type="button"
              data-testid="series-remove"
              class="text-gray-400 hover:text-red-600 dark:hover:text-red-400 disabled:opacity-50"
              :disabled="busy"
              :aria-label="`Remove ${pointLabel(point)} from this series`"
              @click="onRemove(point.document_id, pointLabel(point))"
            >
              ×
            </button>
            <span v-else aria-hidden="true"></span>
          </li>
        </ul>
      </div>
    </div>
  </section>
</template>
