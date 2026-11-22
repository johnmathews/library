<script setup lang="ts">
/**
 * The `/charts` stacked-bar mark (spec §4.4, §4.12).
 *
 * This component draws bands, it does not decide them: `bands()`
 * (`@/spending/palette`) owns the fold, the de-collision walk and any stored
 * colour override, and this component reads `band.colour` and nothing else.
 * The one exception the spec allows is the unsplit chart, where `bands` is
 * `[]` and there is nothing to fold — the single series draws in
 * `chart-spending`, and a period whose refunds exceed its payments (below the
 * zero line) in `chart-income` (design system, Charts).
 *
 * Every colour is a design-system role, resolved for the current theme by
 * `chartColour()` because a canvas cannot read `var()`. Gridlines are
 * `chart-grid`, the zero line `control-border`, tick labels `ink-secondary`
 * at 12.5px (`type-small`), the tooltip at 14px (`type-body`), both in the
 * brand's font, the y ticks as money (`formatTick`). A period with no
 * documents keeps its place on the axis and shows a 3px `quiet` stub at the
 * zero line (`fillPeriods`, `emptyPeriodStub`).
 */
import { computed } from 'vue'
import { Bar } from 'vue-chartjs'
import { BarElement, CategoryScale, Chart as ChartJS, LinearScale, Tooltip } from 'chart.js'
import { useDark } from '@vueuse/core'
import type { ChartData } from '@/api/spending'
import { OTHER_VALUE, type Band } from '@/spending/palette'
import { chartColour, INCOME_COLOUR, SPENDING_COLOUR } from '@/utils/splitPalette'
import { formatMoney, formatTick, fromCents, toCents } from '@/spending/money'
import { fillPeriods } from '@/spending/periods'

// A category scale, not a time scale: the x-axis is uniform periods, so
// "nothing is 2px wide because two invoices landed three days apart" is true
// by construction rather than by configuration (spec §4.4 #3). Registration
// is global and additive, so this must not unregister any scale another
// chart component may have registered elsewhere.
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

/** A design-system role, resolved for the current theme. */
const paint = (colour: string): string => chartColour(colour, isDark.value)

// The surface colour is the 2px gap between touching segments, not a
// stroke: it must match `.card`'s own background (`panel`) so a segment
// boundary reads as a gap onto the page, not an outline around the bar.
const surfaceColour = computed(() => paint('var(--panel)'))

// Isolation (the `hidden` prop) is a display filter over the bands `bands()`
// already assigned — it must never re-run the fold or recolour a survivor,
// so this only ever removes entries, in the same order they arrived in.
const visibleBands = computed<Band[]>(() =>
  props.bands.filter((b) => !(props.hidden?.has(b.value) ?? false)),
)

// The x-axis categories: every period from the first the data carries to the
// last, at the chart's grain, sorted. The API sends no cell for a period with
// no documents, so `fillPeriods` puts those back; otherwise the axis would
// skip them and two bars a month apart would read as continuous. Periods are
// ISO date strings, so a lexicographic sort is a chronological one. Unlike a
// TimeScale, nothing here spaces bars by elapsed time.
const periods = computed<string[]>(() =>
  fillPeriods(
    props.data.cells.map((c) => c.period),
    props.data.grain,
  ),
)

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

/** True for a period the data has no cell for: no documents, drawn as a stub. */
function isEmptyPeriod(index: number): boolean {
  const period = periods.value[index]
  return period === undefined || !cellCents.value.has(period)
}

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
  /** A role reference or stored hex (`Band.colour`); null for the unsplit series, coloured per bar by sign. */
  colour: string | null
  value: string | null | typeof OTHER_VALUE
  cents: number[]
}

// One series per visible band, in band order — the chart never re-derives a
// colour or an order, it reads `band.colour` verbatim. The unsplit case
// (`bands` is `[]`) draws a single series in the spending colour instead,
// since there is no fold to read a band from.
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
    return [
      {
        label: '',
        colour: null,
        value: null,
        cents: periods.value.map((p) => centsForPeriod(p)),
      },
    ]
  }
  return visibleBands.value.map((b) => {
    const keys = b.members.map((m) => keyOf(m.value))
    return {
      label: b.label,
      colour: b.colour,
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

// The tooltip's last line: the hovered bar's total over the bands on show,
// so hiding a band in the legend takes it out of the total as it takes it
// out of the bar. An unsplit chart has one series, so no total line.
function tooltipFooter(items: { dataIndex: number }[]): string {
  if (props.bands.length === 0 || items.length === 0) return ''
  const index = items[0]!.dataIndex
  const cents = series.value.reduce((sum, s) => sum + (s.cents[index] ?? 0), 0)
  return `Total: ${formatMoney(fromCents(cents), props.data.currency)}`
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
// Bar width: about two thirds of each period's slot, so bars stay thick when
// a wide card holds few periods and shrink to fit when it holds many; capped
// so a chart of two or three periods does not draw slabs. Chart.js sizes a
// bar as slot x categoryPercentage x barPercentage; the empty-period stub
// uses the same arithmetic.
const CATEGORY_SHARE = 0.7
const BAR_PERCENT = 0.9
const MAX_BAR = 120
// The surface-coloured gap between stacked segments, drawn as each bar's
// border. Chart.js paints a border inside the bar, so a bar's colour is
// `barWidth - 2 * SEGMENT_GAP` wide; the empty-period stub matches that.
const SEGMENT_GAP = 2
function barWidth(slot: number): number {
  return Math.min(MAX_BAR, slot * CATEGORY_SHARE * BAR_PERCENT)
}

const chartData = computed(() => ({
  labels: periods.value,
  datasets: series.value.map((s, datasetIndex) => ({
    label: s.label,
    data: s.cents.map((c) => c / 100),
    backgroundColor:
      s.colour === null
        ? s.cents.map((c) => paint(c < 0 ? INCOME_COLOUR : SPENDING_COLOUR))
        : paint(s.colour),
    borderWidth: SEGMENT_GAP,
    borderColor: surfaceColour.value,
    categoryPercentage: CATEGORY_SHARE,
    barPercentage: BAR_PERCENT,
    maxBarThickness: MAX_BAR,
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
  if (isEmptyPeriod(index)) return
  const period = periods.value[index]
  if (period === undefined) return
  const s = series.value[datasetIndex]
  if (!s) return
  emit('cell', period, s.value)
}

// The stub for an empty period: 3px of `quiet` at the zero line, as wide as a
// bar, rounded like one. Drawn by a plugin rather than as a dataset, so it has
// no value, no tooltip, no click target and no band, and it stays when the
// legend hides every band (the period is empty either way). The bar width
// is the bars' own (`barWidth`).
const STUB_HEIGHT = 3
interface StubChart {
  ctx: CanvasRenderingContext2D
  scales: Record<string, { width: number; getPixelForValue: (value: number) => number }>
  data: { labels?: unknown[] }
}
const emptyPeriodStub = {
  id: 'emptyPeriodStub',
  afterDatasetsDraw(chart: StubChart): void {
    const labels = chart.data.labels ?? []
    const x = chart.scales.x
    const y = chart.scales.y
    if (!x || !y || labels.length === 0) return
    const width = barWidth(x.width / labels.length) - 2 * SEGMENT_GAP
    const zero = y.getPixelForValue(0)
    const { ctx } = chart
    ctx.save()
    ctx.fillStyle = paint('var(--quiet)')
    labels.forEach((_label, index) => {
      if (!isEmptyPeriod(index)) return
      ctx.beginPath()
      ctx.roundRect(x.getPixelForValue(index) - width / 2, zero - STUB_HEIGHT, width, STUB_HEIGHT, STUB_HEIGHT / 2)
      ctx.fill()
    })
    ctx.restore()
  },
}
const chartPlugins = [emptyPeriodStub]

// A canvas does not inherit the page's font, so the chart names it: the
// brand's `--font-sans`, read once from the tokens. Ticks are `type-small`
// (12.5px), the smallest text in the app; the tooltip is `type-body` (14px),
// its period and total at 600 like a label.
const FALLBACK_FONT = 'system-ui, sans-serif'
const fontFamily =
  (typeof document === 'undefined'
    ? ''
    : getComputedStyle(document.documentElement).getPropertyValue('--font-sans').trim()) || FALLBACK_FONT
const TICK_FONT = { family: fontFamily, size: 12.5 }
const TOOLTIP_FONT = { family: fontFamily, size: 14, lineHeight: 1.5 }
const TOOLTIP_STRONG = { ...TOOLTIP_FONT, weight: 600 }

const tickStyle = computed(() => ({ color: paint('var(--ink-secondary)'), font: TICK_FONT }))

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
    // Hovering any segment shows the whole bar: every series with money in
    // that period, top segment first so the list reads like the bar (bands
    // are largest first from the baseline, `palette.ts`), then the total.
    // `mode` is set on the tooltip only, so a click still drills into the one
    // segment under the pointer.
    tooltip: {
      mode: 'index' as const,
      intersect: true,
      itemSort: (a: { datasetIndex: number }, b: { datasetIndex: number }) => b.datasetIndex - a.datasetIndex,
      // An empty period's zero-height bars, and a series with nothing in the
      // hovered period, are not figures to read.
      filter: (item: { dataIndex: number; raw: unknown }) => !isEmptyPeriod(item.dataIndex) && item.raw !== 0,
      callbacks: { label: tooltipLabel, footer: tooltipFooter },
      titleFont: TOOLTIP_STRONG,
      bodyFont: TOOLTIP_FONT,
      footerFont: TOOLTIP_STRONG,
      padding: 10,
      boxPadding: 4,
    },
  },
  scales: {
    x: {
      type: 'category' as const,
      stacked: true,
      grid: { display: false },
      ticks: props.compact
        ? { ...tickStyle.value, callback: (_value: unknown, index: number) => shortLabel(periods.value[index] ?? '') }
        : tickStyle.value,
    },
    y: {
      stacked: true,
      // A refund exceeding its bucket's payments draws below the baseline;
      // an axis that starts elsewhere hides the sign (spec §4.4 #2).
      beginAtZero: true,
      border: { display: false },
      grid: {
        color: (ctx: { tick?: { value: number } }) =>
          paint(ctx.tick?.value === 0 ? 'var(--control-border)' : 'var(--chart-grid)'),
      },
      // Money as the card writes it: `EUR 200`, `−EUR 100`.
      ticks: props.compact
        ? { display: false }
        : { ...tickStyle.value, callback: (value: number | string) => formatTick(Number(value), props.data.currency) },
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
      :plugins="chartPlugins"
    />
  </div>
</template>
