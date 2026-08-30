<script setup lang="ts">
/**
 * The `/charts/:chartId` workspace (spec §4.6, §4.13): one saved chart,
 * examined in full — the full toolbar (grain / split / range / currency),
 * the stacked chart, the legend, the footer accounting statement, and the
 * drill-through panel that answers a bar, a folded `Other` segment, or a
 * footer bucket.
 *
 * This view fetches the chart once by id (`fetchChart`, never `listCharts`
 * paged looking for a row — the board already proved that pattern wrong for
 * a single-chart page) and re-fetches `/data` whenever a toolbar control
 * changes. **The range filters the data** — `from`/`to` are sent to the API
 * — rather than clamping the axis client-side, so the headline and the
 * drawing can never disagree (§10.3 #2).
 *
 * **`split` is always sent explicitly.** The API reads `split=` (empty) as
 * "no split axis" and an OMITTED `split` as "use the chart's default" —
 * dropping the key when the split control is turned off would silently
 * restore the default (`windowQuery` in `@/api/spending` documents the same
 * trap from the wire side). `splitValue` below is bound directly to the
 * `<select>`'s native value — `''` for "no split", the chart's
 * `default_split` facet key otherwise — and `currentArgs.split` reads it
 * verbatim, every time, never behind a ternary that could omit the key.
 *
 * **Isolation never touches the headline.** `hiddenSplitValues` is a
 * client-side display filter over the legend/chart, exactly like
 * `SpendingCard`'s own — the headline figure reads `data.total` directly and
 * never consults it. A `selectionLine` names the current filter instead, so
 * the promise ("isolating never rewrites the number the API reported") holds
 * without the screen going silent about what's currently isolated.
 *
 * **The panel's presentation is measured, not guessed.** `SpendingDrillPanel`
 * is a native `<dialog>` opened with `showModal()`, which puts it in the
 * browser's top layer — out of flow, unreachable by any `@container` query
 * rooted here. This view therefore measures its own content column with a
 * `ResizeObserver` and hands the panel a resolved `sheet` boolean.
 * `SHEET_THRESHOLD_PX` (48rem × 16px/rem = 768px) is the SAME number the
 * toolbar's own `@3xl/workspace` container query below is keyed to — one
 * named constant, referenced from both, so the two cannot drift apart
 * silently. Never `window.innerWidth`, never `matchMedia`, never a `lg:`
 * class: at a 1280px viewport the content column is 960px with the sidebar
 * expanded and 1136px with it collapsed, and no viewport query can tell
 * those two apart (this is the defect docs/frontend-view-principles.md §5.1
 * already names, reintroduced a third time).
 *
 * **The chart is dimmed, never unmounted, while a refetch is in flight.**
 * `SpendingChart` carries no loading signal of its own and never keys its
 * `<Bar>` on `data` (task 3) — `data` here is only ever replaced on a
 * SUCCESSFUL fetch, never nulled out first, so the previous render stays on
 * screen the whole time `dataBusy` is true. `data-busy` is a plain DOM
 * attribute (mirroring `SpendingDrillPanel`'s own `data-presentation`)
 * rather than a class, so a test can assert the outcome without asserting a
 * Tailwind utility name.
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  cellArgs,
  fetchChart,
  fetchChartData,
  type Chart,
  type ChartArgs,
  type ChartData,
  type FooterBucket,
  type Grain,
  type SplitValue,
} from '@/api/spending'
import { ApiError } from '@/api/client'
import { useCurrencyOptions } from '@/composables/useCurrencyOptions'
import { bands, OTHER_VALUE, type Band } from '@/spending/palette'
import { formatMoney } from '@/spending/money'
import { AppBackLink, PageHeader } from '@/components/app'
import CurrencySelect from '@/components/CurrencySelect.vue'
import SpendingChart from '@/components/spending/SpendingChart.vue'
import SpendingLegend from '@/components/spending/SpendingLegend.vue'
import SpendingFooter from '@/components/spending/SpendingFooter.vue'
import SpendingDrillPanel from '@/components/spending/SpendingDrillPanel.vue'
import DrillCellBody from '@/components/spending/DrillCellBody.vue'
import DrillBucketBody from '@/components/spending/DrillBucketBody.vue'
import DrillOtherBody from '@/components/spending/DrillOtherBody.vue'

const route = useRoute()
const { addOption: addCurrencyOption } = useCurrencyOptions()

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.detail : 'Something went wrong — check your connection and try again.'
}

const chartId = computed<number>(() => Number(route.params.chartId))

// --- Chart + toolbar state --------------------------------------------------

const chart = ref<Chart | null>(null)
const chartLoading = ref(true)
const chartError = ref<string | null>(null)

const GRAIN_OPTIONS: { value: Grain; label: string }[] = [
  { value: 'week', label: 'Week' },
  { value: 'month', label: 'Month' },
  { value: 'quarter', label: 'Quarter' },
  { value: 'year', label: 'Year' },
]
const GRAIN_LABELS: Record<Grain, string> = { week: 'Weekly', month: 'Monthly', quarter: 'Quarterly', year: 'Yearly' }

const grain = ref<Grain>('month')
/** The native `<select>`'s own value: `''` is "no split", otherwise the
 * chart's `default_split` facet key. Never anything else — this chart has
 * exactly one split axis to offer, never a choice among several. */
const splitValue = ref<string>('')
const currency = ref<string>('')
const from = ref<string>('')
const to = ref<string>('')

/** The window arguments `/data` and, via `cellArgs`, `/cell` must agree on.
 * `split` is read from `splitValue` unconditionally — never a ternary that
 * could drop the key when it is empty. */
const currentArgs = computed<ChartArgs>(() => ({
  grain: grain.value,
  split: splitValue.value,
  currency: currency.value,
  from: from.value || undefined,
  to: to.value || undefined,
}))

function initControlsFromChart(c: Chart): void {
  grain.value = c.default_grain
  splitValue.value = c.default_split ?? ''
  currency.value = c.display_currency
  from.value = ''
  to.value = ''
  // So the workspace's own currency select always has an <option> matching
  // the chart's current currency, even one no one has added here before.
  addCurrencyOption(c.display_currency)
}

// --- Data load ---------------------------------------------------------------

const data = ref<ChartData | null>(null)
const dataBusy = ref(false)
const dataError = ref<string | null>(null)

async function loadData(): Promise<void> {
  const c = chart.value
  if (!c) return
  dataBusy.value = true
  dataError.value = null
  try {
    // Never reset `data.value` to null first — the previous render stays on
    // screen (dimmed via `dataBusy`) for exactly as long as this is in
    // flight, which is the whole point of the "never unmounted" contract.
    data.value = await fetchChartData(c.id, currentArgs.value)
  } catch (err) {
    dataError.value = errorText(err)
  } finally {
    dataBusy.value = false
  }
}

let stopArgsWatch: (() => void) | null = null

async function loadChart(): Promise<void> {
  chartLoading.value = true
  chartError.value = null
  chart.value = null
  data.value = null
  dataError.value = null
  drill.value = null
  stopArgsWatch?.()
  stopArgsWatch = null

  if (!Number.isFinite(chartId.value)) {
    chartError.value = 'Unknown chart.'
    chartLoading.value = false
    return
  }

  try {
    // GET /api/spending/{id} — a single chart's definition. Never
    // `listCharts` paged looking for a matching row.
    chart.value = await fetchChart(chartId.value)
  } catch (err) {
    chartError.value = errorText(err)
    chartLoading.value = false
    return
  }
  chartLoading.value = false

  initControlsFromChart(chart.value)
  await loadData()

  // Set up only AFTER the first load, so hydrating the controls from the
  // chart's own defaults above never fires a redundant second fetch.
  stopArgsWatch = watch(currentArgs, () => {
    drill.value = null // stale echoed args would answer the wrong question
    void loadData()
  })
}

onMounted(loadChart)
watch(() => chartId.value, loadChart)
onBeforeUnmount(() => stopArgsWatch?.())

// --- Bands, legend, headline ------------------------------------------------

const workspaceBands = computed<Band[]>(() => (data.value ? bands(data.value.splits, data.value.cells) : []))

const hiddenSplitValues = ref<Set<string | null | typeof OTHER_VALUE>>(new Set())

function onIsolate(key: string | null | typeof OTHER_VALUE): void {
  const all = workspaceBands.value.map((b) => b.value)
  const alreadyIsolated = all.length - hiddenSplitValues.value.size === 1 && !hiddenSplitValues.value.has(key)
  hiddenSplitValues.value = alreadyIsolated ? new Set() : new Set(all.filter((v) => v !== key))
}
function onExclude(key: string | null | typeof OTHER_VALUE): void {
  const next = new Set(hiddenSplitValues.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  hiddenSplitValues.value = next
}
function onLegendReset(): void {
  hiddenSplitValues.value = new Set()
}

const headlineFigure = computed<string | null>(() =>
  data.value ? formatMoney(data.value.total, data.value.currency) : null,
)

/** Names the current isolate/exclude filter WITHOUT touching the headline
 * above, which always reads `data.total` directly (spec §4.7). */
const selectionLine = computed<string | null>(() => {
  if (hiddenSplitValues.value.size === 0) return null
  const visible = workspaceBands.value.filter((b) => !hiddenSplitValues.value.has(b.value))
  if (visible.length === 1) return `Showing ${visible[0]!.label}`
  const hiddenLabels = workspaceBands.value
    .filter((b) => hiddenSplitValues.value.has(b.value))
    .map((b) => b.label)
  return `Hiding ${hiddenLabels.join(', ')}`
})

// --- Drill panel --------------------------------------------------------------

type Drill =
  | { kind: 'cell'; period: string; splitValue: string | null }
  | { kind: 'other'; period: string; members: SplitValue[] }
  | { kind: 'bucket'; bucket: FooterBucket; amountKind?: string }

const drill = ref<Drill | null>(null)
const panelOpen = computed(() => drill.value !== null)

/** `/cell` gets `/data`'s echoed arguments verbatim, plus the cell's own
 * `period` — never a fresh set built from the toolbar's CURRENT state, which
 * could have moved on since the bar was drawn. `data` never changes while
 * the panel is open (the args-watcher above closes it first), so this is
 * safe to read once per open panel. */
const drillArgs = computed<ChartArgs>(() => (data.value ? cellArgs(data.value) : {}))

function onChartCell(period: string, value: string | null | typeof OTHER_VALUE): void {
  if (value === OTHER_VALUE) {
    // The folded values' totals for this period are already in `/data`'s
    // cells — this costs no request (spec §4.12 "Other drills in two
    // steps").
    const other = workspaceBands.value.find((b) => b.isOther)
    drill.value = { kind: 'other', period, members: other?.members ?? [] }
    return
  }
  drill.value = { kind: 'cell', period, splitValue: value }
}

function onOtherPick(value: string | null): void {
  if (drill.value?.kind !== 'other') return
  drill.value = { kind: 'cell', period: drill.value.period, splitValue: value }
}

function onFooterBucket(bucket: FooterBucket, amountKind?: string): void {
  drill.value = { kind: 'bucket', bucket, amountKind }
}

function closeDrill(): void {
  drill.value = null
}

const BUCKET_LABELS: Record<FooterBucket, string> = {
  excluded: 'Excluded',
  unclassified: 'Unclassified',
  uncategorised: 'Uncategorised',
  undated: 'Undated',
  unaccounted: 'Unaccounted',
}

const drillTitle = computed<string>(() => {
  const d = drill.value
  const c = chart.value
  if (!d || !c) return ''
  return d.kind === 'bucket' ? `${c.name} · ${BUCKET_LABELS[d.bucket]}` : `${c.name} · ${d.period}`
})

// --- Container-measured presentation (spec §4.13) ---------------------------

/** 48rem at the app's fixed 16px root font-size — the SAME number the
 * `@3xl/workspace` container query below is keyed to (see the comment next
 * to that class). Defined once so the two cannot drift apart silently. */
const SHEET_THRESHOLD_PX = 48 * 16

const workspaceRoot = ref<HTMLElement | null>(null)
const isSheet = ref(false)
let resizeObserver: ResizeObserver | null = null

onMounted(() => {
  const el = workspaceRoot.value
  if (!el || typeof ResizeObserver === 'undefined') return
  resizeObserver = new ResizeObserver((entries) => {
    const entry = entries[0]
    if (!entry) return
    isSheet.value = entry.contentRect.width < SHEET_THRESHOLD_PX
  })
  resizeObserver.observe(el)
})
onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  resizeObserver = null
})

// --- Mobile toolbar chip (spec §4.13) ---------------------------------------
//
// Below the threshold the full row (below) is CSS-hidden and this chip takes
// its place. It is a real control, not a static label: tapping it reveals
// the SAME controls in place, so every setting stays reachable at every
// width — never a wide-only capability quietly dropped (frontend-view-
// principles.md §4).

const mobileControlsOpen = ref(false)

const chipSummary = computed<string>(() => {
  const parts = [GRAIN_LABELS[grain.value]]
  if (from.value || to.value) parts.push(`${from.value || '…'}–${to.value || '…'}`)
  if (splitValue.value) parts.push(`Split: ${splitValue.value}`)
  if (currency.value) parts.push(currency.value)
  return parts.join(' · ')
})

// `@3xl/workspace` = 48rem = 768px — the SAME threshold as SHEET_THRESHOLD_PX
// above (see the comment there). One number, referenced from both the
// ResizeObserver check and this Tailwind container-query breakpoint, so a
// change to one that isn't mirrored in the other is a merge/stack boundary
// that silently disagrees with the drill-panel's sheet-vs-modal boundary.
const toolbarRowClass = computed<string>(() =>
  mobileControlsOpen.value
    ? 'flex flex-wrap items-end gap-3'
    : 'hidden flex-wrap items-end gap-3 @3xl/workspace:flex',
)
</script>

<template>
  <div id="spending-workspace-view" ref="workspaceRoot" class="@container/workspace">
    <AppBackLink to="/charts" text="Back to charts" class="mb-4" />

    <p v-if="chartLoading" data-testid="workspace-loading" class="text-gray-500 dark:text-gray-400">Loading…</p>

    <p v-else-if="chartError" data-testid="workspace-load-error" class="text-red-600 dark:text-red-400">
      {{ chartError }}
    </p>

    <template v-else-if="chart">
      <PageHeader :title="chart.name" :description="chart.question_text">
        <template #controls>
          <div data-testid="workspace-toolbar-chip" class="@3xl/workspace:hidden">
            <button
              type="button"
              data-testid="workspace-toolbar-chip-button"
              class="filter-label rounded-lg border border-gray-200 px-3 py-2 text-left dark:border-gray-700/60"
              :aria-expanded="mobileControlsOpen"
              @click="mobileControlsOpen = !mobileControlsOpen"
            >
              {{ chipSummary }}
            </button>
          </div>

          <div data-testid="workspace-toolbar" :class="toolbarRowClass">
            <div>
              <label class="filter-label" for="workspace-grain">Grain</label>
              <select id="workspace-grain" v-model="grain" data-testid="workspace-grain" class="form-select">
                <option v-for="opt in GRAIN_OPTIONS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
              </select>
            </div>

            <div v-if="chart.default_split">
              <label class="filter-label" for="workspace-split">Split</label>
              <select id="workspace-split" v-model="splitValue" data-testid="workspace-split" class="form-select">
                <option :value="chart.default_split">By {{ chart.default_split }}</option>
                <option value="">No split</option>
              </select>
            </div>

            <div>
              <label class="filter-label" for="workspace-from">From</label>
              <input
                id="workspace-from"
                v-model="from"
                type="date"
                data-testid="workspace-from"
                class="form-input"
                aria-label="Range start date"
              />
            </div>

            <div>
              <label class="filter-label" for="workspace-to">To</label>
              <input
                id="workspace-to"
                v-model="to"
                type="date"
                data-testid="workspace-to"
                class="form-input"
                aria-label="Range end date"
              />
            </div>

            <div class="w-28 shrink-0" data-testid="workspace-currency-select">
              <span class="filter-label" aria-hidden="true">Currency</span>
              <CurrencySelect v-model="currency" />
            </div>
          </div>
        </template>
      </PageHeader>

      <p v-if="dataError" data-testid="workspace-data-error" class="text-red-600 dark:text-red-400">
        {{ dataError }}
      </p>

      <div
        v-else-if="data"
        data-testid="workspace-chart-region"
        :data-busy="dataBusy ? 'true' : 'false'"
        class="flex flex-col gap-4"
        :class="{ 'opacity-50 transition-opacity': dataBusy }"
      >
        <div data-testid="workspace-headline">
          <p
            data-testid="workspace-headline-figure"
            class="text-2xl font-semibold tabular-nums text-gray-900 dark:text-gray-50"
          >
            {{ headlineFigure }}
          </p>
          <p
            v-if="selectionLine"
            data-testid="workspace-selection"
            class="text-sm text-gray-500 dark:text-gray-400"
          >
            {{ selectionLine }}
          </p>
        </div>

        <div class="h-96 w-full">
          <SpendingChart :data="data" :bands="workspaceBands" :hidden="hiddenSplitValues" @cell="onChartCell" />
        </div>

        <SpendingLegend
          :bands="workspaceBands"
          :hidden="hiddenSplitValues"
          :currency="data.currency"
          @isolate="onIsolate"
          @exclude="onExclude"
          @reset="onLegendReset"
        />

        <SpendingFooter :data="data" @bucket="onFooterBucket" />
      </div>

      <SpendingDrillPanel :open="panelOpen" :title="drillTitle" :sheet="isSheet" @close="closeDrill">
        <DrillCellBody
          v-if="drill?.kind === 'cell'"
          :chart-id="chart.id"
          :period="drill.period"
          :split-value="drill.splitValue"
          :args="drillArgs"
          :chart-name="chart.name"
        />
        <DrillBucketBody
          v-else-if="drill?.kind === 'bucket'"
          :chart-id="chart.id"
          :bucket="drill.bucket"
          :amount-kind="drill.amountKind"
          :args="drillArgs"
        />
        <DrillOtherBody
          v-else-if="drill?.kind === 'other'"
          :period="drill.period"
          :members="drill.members"
          :cells="data?.cells ?? []"
          :currency="data?.currency ?? currency"
          @pick="onOtherPick"
        />
      </SpendingDrillPanel>
    </template>
  </div>
</template>
