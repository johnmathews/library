<script setup lang="ts">
/**
 * `/charts` board (spec §4.2, §10.1): one card per saved chart, ordered by
 * `ordinal` then name — **never** by document count, which the API response
 * carries nowhere on `Chart` in the first place, but which a card's *data*
 * does (via `ChartData.documents`) and must never leak into sort order.
 *
 * Every chart's data is fetched in parallel (`Promise.allSettled`) so one
 * chart's failure renders inline on that card (`SpendingCard`'s own `error`
 * prop) and never as a page-level banner that would hide the charts that DID
 * load. `SpendingCard` renders no body while `busy` is true and `data` is
 * still null (there is nothing to show yet, dimmed or otherwise), so a first
 * load would otherwise be a grid of empty rectangles — this view owns the
 * load lifecycle, so it owns that placeholder too.
 *
 * Reordering is one function, two triggers (spec §4.2): the keyboard "move
 * up" / "move down" path from a card's overflow menu, and whole-card drag via
 * SortableJS (already a dependency; the established pattern is
 * `DashboardFieldsEditor.vue`). Only the ordinals that actually moved are
 * PATCHed — with three charts at ordinals 0, 1, 2, moving the first down
 * swaps it with the second and leaves the third alone.
 *
 * The board's own currency choice — which `QuestionDraft` (the header's
 * "ask a question" input) and `SpendingEmptyState` create new charts in —
 * is picked via `CurrencySelect` (built on `useCurrencyOptions()`) and
 * persisted per-machine in `localStorage`, exactly like `useCurrencyOptions`
 * persists its own custom codes: this is a display preference about this
 * screen, not something the API remembers. It defaults to the first
 * configured option (`EUR`) until the owner changes it.
 */
import { onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useStorage } from '@vueuse/core'
import Sortable from 'sortablejs'
import {
  deleteChart,
  fetchChartData,
  listCharts,
  updateChart,
  MAX_LIMIT,
  type Chart,
  type ChartData,
} from '@/api/spending'
import { ApiError } from '@/api/client'
import { useCurrencyOptions } from '@/composables/useCurrencyOptions'
import { PageHeader } from '@/components/app'
import CurrencySelect from '@/components/CurrencySelect.vue'
import QuestionDraft from '@/components/spending/QuestionDraft.vue'
import SpendingEmptyState from '@/components/spending/SpendingEmptyState.vue'
import SpendingCard from '@/components/spending/SpendingCard.vue'

const { options: currencyOptions } = useCurrencyOptions()
// Persisted per-machine, like `useCurrencyOptions` itself — a display
// preference about THIS screen, not an API-remembered value. Defaults to the
// first configured option (`EUR`) until changed via `CurrencySelect` below.
const currency = useStorage<string>('library:charts-board-currency', currencyOptions.value[0] ?? 'EUR')

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.detail : 'Something went wrong — check your connection and try again.'
}

// --- List load -------------------------------------------------------------

const charts = ref<Chart[]>([])
const loading = ref(true)
const loadError = ref<string | null>(null)

/** Ordinal first, name as the tie-break — never document count (spec §4.2,
 * §10.1). Document counts live on a card's *data*, never on `Chart` itself,
 * but this is deliberate: the sort must not be tempted to reach into it. */
function sortCharts(list: Chart[]): Chart[] {
  return [...list].sort((a, b) => a.ordinal - b.ordinal || a.name.localeCompare(b.name))
}

// --- Per-card data (one entry per chart id) --------------------------------

interface CardState {
  data: ChartData | null
  error: string | null
  busy: boolean
}

const cardState = reactive(new Map<number, CardState>())

function stateFor(chartId: number): CardState {
  // Always return the value read BACK from the reactive `Map`, never the
  // freshly-built object handed to `.set()` — that object is the raw,
  // unwrapped value, and mutating it later bypasses the proxy entirely: the
  // underlying data ends up correct but nothing tells Vue to re-render.
  if (!cardState.has(chartId)) {
    cardState.set(chartId, { data: null, error: null, busy: true })
  }
  return cardState.get(chartId)!
}

/** A card whose first load has not yet resolved — `SpendingCard` renders
 * nothing for this combination, so the board substitutes a placeholder. */
function isFirstLoadPending(chartId: number): boolean {
  const state = cardState.get(chartId)
  return state !== undefined && state.busy && state.data === null && state.error === null
}

async function loadCard(chart: Chart): Promise<void> {
  const state = stateFor(chart.id)
  state.busy = true
  try {
    state.data = await fetchChartData(chart.id, {})
    state.error = null
  } catch (err) {
    state.error = errorText(err)
  } finally {
    state.busy = false
  }
}

async function loadAll(): Promise<void> {
  loading.value = true
  loadError.value = null
  try {
    charts.value = sortCharts(await listCharts(MAX_LIMIT))
  } catch (err) {
    loadError.value = errorText(err)
    loading.value = false
    return
  }
  loading.value = false
  for (const chart of charts.value) stateFor(chart.id)
  // Parallel, and each failure stays on its own card — see the header note.
  await Promise.allSettled(charts.value.map((chart) => loadCard(chart)))
}

onMounted(loadAll)

async function onChartCreated(chart: Chart): Promise<void> {
  charts.value = sortCharts([...charts.value, chart])
  await loadCard(chart)
}

// --- Reorder: one function, two triggers ------------------------------------

const reorderError = ref<string | null>(null)

async function reorder(fromIndex: number, toIndex: number): Promise<void> {
  if (toIndex < 0 || toIndex >= charts.value.length || fromIndex === toIndex) return
  // `fromIndex` always names a real row here: it comes either from a card's
  // own position in this same `charts` array (the keyboard path) or from
  // SortableJS's `oldIndex` on a card it actually dragged out of this grid
  // (the drag path) — never from outside input.
  const moved = charts.value[fromIndex]!

  // Kept so a failed PATCH can restore exactly what the board showed before
  // this drag/click — never left showing an order the server rejected.
  const previous = charts.value

  const next = [...charts.value]
  next.splice(fromIndex, 1)
  next.splice(toIndex, 0, moved)

  // Only the charts whose ordinal actually moves get PATCHed — compare
  // against the ordinal each chart carried BEFORE this reassignment.
  const changed = next
    .map((chart, ordinal) => ({ chart, ordinal }))
    .filter(({ chart, ordinal }) => chart.ordinal !== ordinal)

  charts.value = next.map((chart, ordinal) => ({ ...chart, ordinal }))
  reorderError.value = null
  try {
    await Promise.all(changed.map(({ chart, ordinal }) => updateChart(chart.id, { ordinal })))
  } catch (err) {
    // `Promise.all` fires every changed PATCH in parallel and rejects as
    // soon as ONE does — the others already in flight can still land, so the
    // server may now hold a partially-applied reorder even though this
    // branch is reached. Nothing client-side can undo that partial write (no
    // compensating transaction to run), but the LOCAL order is rolled back
    // so the screen at least stops claiming an order the server did not
    // fully accept; a full reload reconciles from whatever the server
    // actually ended up with either way (`sortCharts` falls back to name
    // order for any chart the reorder left with a duplicate ordinal).
    charts.value = previous
    reorderError.value = errorText(err)
  }
}

function moveUp(index: number): void {
  void reorder(index, index - 1)
}
function moveDown(index: number): void {
  void reorder(index, index + 1)
}

// Whole-card drag (no handle — the card face carries no reorder controls of
// its own, spec §10.3 #5) calls the exact same function as the keyboard path.
const gridEl = ref<HTMLElement | null>(null)
let sortable: Sortable | null = null

watch(gridEl, (el) => {
  sortable?.destroy()
  sortable = null
  if (el) {
    sortable = Sortable.create(el, {
      animation: 150,
      onEnd: (evt) => {
        if (evt.oldIndex == null || evt.newIndex == null) return
        void reorder(evt.oldIndex, evt.newIndex)
      },
    })
  }
})

onBeforeUnmount(() => {
  sortable?.destroy()
  sortable = null
})

// --- Rename / delete -----------------------------------------------------------

// `SpendingCard` owns the PATCH itself (a chart's name is card-local, not
// something the board polls) and hands back the server's response once it
// succeeds — this just keeps the board's own `charts` array in sync so the
// new name survives a re-render and, if it changed the sort tie-break,
// re-sorts.
function onRenamed(updated: Chart): void {
  charts.value = sortCharts(charts.value.map((c) => (c.id === updated.id ? updated : c)))
}

async function onDelete(chart: Chart): Promise<void> {
  const state = stateFor(chart.id)
  try {
    await deleteChart(chart.id)
    charts.value = charts.value.filter((c) => c.id !== chart.id)
    cardState.delete(chart.id)
  } catch (err) {
    state.error = errorText(err)
  }
}
</script>

<template>
  <div id="spending-board-view">
    <PageHeader title="Charts" description="Saved questions over your document archive, answered as spending over time.">
      <template #controls>
        <div class="flex flex-wrap items-end gap-3">
          <QuestionDraft :currency="currency" @saved="onChartCreated" />
          <div class="w-28 shrink-0" data-testid="board-currency-select">
            <CurrencySelect v-model="currency" />
          </div>
        </div>
      </template>
    </PageHeader>

    <p v-if="loading" data-testid="board-loading" class="text-gray-500 dark:text-gray-400">Loading…</p>

    <p v-else-if="loadError" data-testid="board-load-error" class="text-red-600 dark:text-red-400">
      {{ loadError }}
    </p>

    <SpendingEmptyState v-else-if="charts.length === 0" :currency="currency" @created="onChartCreated" />

    <div v-else class="@container">
      <p v-if="reorderError" data-testid="board-reorder-error" class="mb-3 text-sm text-red-600 dark:text-red-400">
        {{ reorderError }}
      </p>
      <div
        ref="gridEl"
        class="grid grid-cols-1 gap-4 @2xl:grid-cols-2 @5xl:grid-cols-3"
        data-testid="spending-board-grid"
      >
        <template v-for="(chart, index) in charts" :key="chart.id">
          <div
            v-if="isFirstLoadPending(chart.id)"
            class="card flex h-48 animate-pulse flex-col gap-3 p-5"
            data-testid="spending-card-placeholder"
            :aria-label="`Loading ${chart.name}`"
          >
            <div class="h-4 w-2/3 rounded bg-gray-200 dark:bg-gray-700/60" />
            <div class="h-8 w-1/2 rounded bg-gray-200 dark:bg-gray-700/60" />
            <div class="mt-auto h-16 w-full rounded bg-gray-200 dark:bg-gray-700/60" />
          </div>
          <SpendingCard
            v-else
            :chart="chart"
            :data="cardState.get(chart.id)?.data ?? null"
            :error="cardState.get(chart.id)?.error ?? null"
            :busy="cardState.get(chart.id)?.busy ?? false"
            :can-move-up="index > 0"
            :can-move-down="index < charts.length - 1"
            @renamed="onRenamed"
            @delete="onDelete(chart)"
            @move-up="moveUp(index)"
            @move-down="moveDown(index)"
          />
        </template>
      </div>
    </div>
  </div>
</template>
