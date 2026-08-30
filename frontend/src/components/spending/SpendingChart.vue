<script setup lang="ts">
/**
 * The `/charts` stacked-bar mark (spec §4.4, §4.12).
 *
 * This component draws bands, it does not decide them: `bands()`
 * (`@/spending/palette`) owns the fold, the de-collision walk and any stored
 * colour override, and this component reads `band.light` / `band.dark` and
 * nothing else. The one exception the spec allows is the unsplit chart,
 * where `bands` is `[]` and there is nothing to fold — the single series
 * draws in the first shared-palette slot (`SPLIT_PALETTE[0]`).
 */
import { computed } from 'vue'
import { Bar } from 'vue-chartjs'
import { BarElement, CategoryScale, Chart as ChartJS, LinearScale, Tooltip } from 'chart.js'
import { useDark } from '@vueuse/core'
import type { ChartData } from '@/api/spending'
import { OTHER_VALUE, type Band } from '@/spending/palette'
import { SPLIT_PALETTE } from '@/utils/splitPalette'
import { formatMoney, fromCents, toCents } from '@/spending/money'

// A category scale, not a time scale: the x-axis is uniform periods, so
// "nothing is 2px wide because two invoices landed three days apart" is true
// by construction rather than by configuration (spec §4.4 #3). Registration
// is global and additive — SeriesChartTile.vue's TimeScale registration
// stays; both scales coexist.
ChartJS.register(CategoryScale, LinearScale, BarElement, Tooltip)

const props = defineProps<{
  data: ChartData
  bands: Band[]
  hidden?: Set<string | null | symbol>
  compact?: boolean
}>()

const emit = defineEmits<{ cell: [period: string, splitValue: string | null | typeof OTHER_VALUE] }>()

// Same call as ThemeToggle.vue — one source of truth for "is dark active"
// (`@vueuse/core`, reactive off the `html` class, so a computed over it
// re-renders the chart on the toggle with no watcher of our own).
const isDark = useDark({ selector: 'html' })

// The surface colour is the 2px gap between touching segments, not a
// stroke: it must match `.card`'s own background so a segment boundary
// reads as a gap onto the page, not an outline around the bar.
const surfaceColour = computed(() => (isDark.value ? '#1f2937' : '#ffffff'))

// Isolation (the `hidden` prop) is a display filter over the bands `bands()`
// already assigned — it must never re-run the fold or recolour a survivor,
// so this only ever removes entries, in the same order they arrived in.
const visibleBands = computed<Band[]>(() =>
  props.bands.filter((b) => !(props.hidden?.has(b.value) ?? false)),
)

// The x-axis categories: every distinct period the data carries, sorted —
// periods are ISO date strings, so a lexicographic sort is a chronological
// one. Unlike a TimeScale, nothing here spaces bars by elapsed time.
const periods = computed<string[]>(() => {
  const set = new Set<string>()
  for (const c of props.data.cells) set.add(c.period)
  return Array.from(set).sort()
})

// A `null` split value cannot be a Map key alongside real strings without
// collapsing onto the string "null", so it gets a sentinel that no real
// facet value can produce.
const NULL_KEY = '__null__'
const keyOf = (v: string | null): string => (v === null ? NULL_KEY : v)

// period -> split-value-key -> exact integer cents. Built once per `data`
// change; every per-band, per-period lookup below reads from it rather than
// re-scanning `cells`.
const cellCents = computed<Map<string, Map<string, number>>>(() => {
  const byPeriod = new Map<string, Map<string, number>>()
  for (const c of props.data.cells) {
    let byKey = byPeriod.get(c.period)
    if (!byKey) {
      byKey = new Map<string, number>()
      byPeriod.set(c.period, byKey)
    }
    const k = keyOf(c.split_value)
    byKey.set(k, (byKey.get(k) ?? 0) + toCents(c.total))
  }
  return byPeriod
})

function centsForKeys(period: string, keys: string[]): number {
  const byKey = cellCents.value.get(period)
  if (!byKey) return 0
  let sum = 0
  for (const k of keys) sum += byKey.get(k) ?? 0
  return sum
}

function centsForPeriod(period: string): number {
  const byKey = cellCents.value.get(period)
  if (!byKey) return 0
  let sum = 0
  for (const v of byKey.values()) sum += v
  return sum
}

interface Series {
  label: string
  light: string
  dark: string
  value: string | null | typeof OTHER_VALUE
  cents: number[]
}

// One series per visible band, in band order — the chart never re-derives a
// colour or an order, it reads `band.light` / `band.dark` verbatim. The
// unsplit case (`bands` is `[]`) draws a single series in the first shared
// palette slot instead, since there is no fold to read a band from.
//
// The sentinel MUST be `props.bands.length === 0`, never
// `visibleBands.value.length === 0` — the latter is also true when every
// band has been hidden via the legend's isolate/exclude filter, and
// `centsForPeriod` sums ALL cells for a period, hidden ones included. Keying
// the unsplit fallback on that would redraw a filtered-to-nothing chart as a
// single series at the grand total, directly under a selection line that
// says everything is hidden — the exact recolour-on-filter defect §4.12 #4
// exists to prevent. When `bands` is non-empty but `visibleBands` is empty,
// falling through to the `else` branch below is correct: `.map()` over an
// empty array returns `[]`, so the chart draws zero datasets — an empty
// chart is the honest answer to "everything is hidden".
const series = computed<Series[]>(() => {
  if (props.bands.length === 0) {
    const slot = SPLIT_PALETTE[0]!
    return [
      {
        label: '',
        light: slot.light,
        dark: slot.dark,
        value: null,
        cents: periods.value.map((p) => centsForPeriod(p)),
      },
    ]
  }
  return visibleBands.value.map((b) => {
    const keys = b.members.map((m) => keyOf(m.value))
    return {
      label: b.label,
      light: b.light,
      dark: b.dark,
      value: b.value,
      cents: periods.value.map((p) => centsForKeys(p, keys)),
    }
  })
})

// Rounded corners go on the outermost segment of a bar only, square at the
// baseline — a blanket radius rounds every interior segment and reads as
// separate pills rather than one stacked total. "Outermost" is decided per
// side of zero: the last dataset (in stack order) carrying a positive value
// is the top of the positive stack; the last one carrying a negative value
// is the bottom of the negative stack (a refund exceeding its bucket's
// payments, spec §4.4 #2).
interface FullBorderRadius {
  topLeft: number
  topRight: number
  bottomLeft: number
  bottomRight: number
}
const SQUARE: FullBorderRadius = { topLeft: 0, topRight: 0, bottomLeft: 0, bottomRight: 0 }
const TOP_ROUNDED: FullBorderRadius = { ...SQUARE, topLeft: 4, topRight: 4 }
const BOTTOM_ROUNDED: FullBorderRadius = { ...SQUARE, bottomLeft: 4, bottomRight: 4 }

function borderRadiusAt(datasetIndex: number, dataIndex: number): FullBorderRadius {
  const values = series.value.map((s) => s.cents[dataIndex] ?? 0)
  const value = values[datasetIndex] ?? 0
  if (value === 0) return SQUARE
  if (value > 0) {
    let outer = -1
    values.forEach((v, i) => {
      if (v > 0) outer = i
    })
    return datasetIndex === outer ? TOP_ROUNDED : SQUARE
  }
  let outer = -1
  values.forEach((v, i) => {
    if (v < 0) outer = i
  })
  return datasetIndex === outer ? BOTTOM_ROUNDED : SQUARE
}

function tooltipLabel(ctx: { datasetIndex: number; dataIndex: number }): string {
  const s = series.value[ctx.datasetIndex]
  if (!s) return ''
  const money = formatMoney(fromCents(s.cents[ctx.dataIndex] ?? 0), props.data.currency)
  return s.label ? `${s.label}: ${money}` : money
}

// Chart.js's data/options are read fresh on every prop change, but the
// component itself is never keyed or v-if'd on `data` — the previous canvas
// stays mounted and Chart.js patches it in place, so a refetch never flashes
// a skeleton or shifts the layout. The reduced-opacity treatment while a
// refetch is in flight is the consuming card's job (`SpendingCard`'s own
// `busy` prop, task 7) — this component has no loading signal of its own to
// drive one.
const chartData = computed(() => ({
  labels: periods.value,
  datasets: series.value.map((s, datasetIndex) => ({
    label: s.label,
    data: s.cents.map((c) => c / 100),
    backgroundColor: isDark.value ? s.dark : s.light,
    borderWidth: 2,
    borderColor: surfaceColour.value,
    maxBarThickness: 24,
    borderRadius: (ctx: { dataIndex: number }) => borderRadiusAt(datasetIndex, ctx.dataIndex),
  })),
}))

// The board card shortens periods to yyyy-mm and drops y-axis ticks — never
// the data, only the tick treatment.
function shortLabel(period: string): string {
  return period.length >= 10 ? period.slice(0, 7) : period
}

interface ActiveBarElement {
  datasetIndex: number
  index: number
}
function onBarClick(_event: unknown, elements: ActiveBarElement[]): void {
  if (!elements.length) return
  const { datasetIndex, index } = elements[0]!
  const period = periods.value[index]
  if (period === undefined) return
  const s = series.value[datasetIndex]
  if (!s) return
  emit('cell', period, s.value)
}

const chartOptions = computed(() => ({
  responsive: true,
  maintainAspectRatio: false,
  onClick: onBarClick,
  plugins: {
    // The legend is SpendingLegend.vue's job (task 4), reading the same
    // `bands` prop — Chart.js's own legend would re-list datasets from a
    // second source of truth.
    legend: { display: false },
    // No `title` callback: Chart.js's own default renders the hovered bar's
    // x-axis category label, which is exactly the period string here
    // (`chartData.labels` is `periods`, a category scale) — so the default
    // is already correct and is left alone rather than reimplemented.
    tooltip: { callbacks: { label: tooltipLabel } },
  },
  scales: {
    x: {
      type: 'category' as const,
      stacked: true,
      grid: { display: false },
      ticks: props.compact
        ? { callback: (_value: unknown, index: number) => shortLabel(periods.value[index] ?? '') }
        : {},
    },
    y: {
      stacked: true,
      // A refund exceeding its bucket's payments draws below the baseline;
      // an axis that starts elsewhere hides the sign (spec §4.4 #2).
      beginAtZero: true,
      ticks: props.compact ? { display: false } : {},
    },
  },
}))
</script>

<template>
  <div
    class="relative h-full w-full"
    data-testid="spending-chart"
  >
    <Bar
      :data="chartData"
      :options="chartOptions"
    />
  </div>
</template>
