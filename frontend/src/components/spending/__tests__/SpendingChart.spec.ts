import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'

vi.mock('vue-chartjs', () => ({
  Bar: { name: 'Bar', props: ['data', 'options', 'plugins'], template: '<canvas />' },
}))

import SpendingChart from '../SpendingChart.vue'
import { OTHER_VALUE, OTHER_COLOUR, type Band } from '@/spending/palette'
import { SPLIT_PALETTE } from '@/utils/splitPalette'

// jsdom does not load tokens.css, so the roles the chart reads are declared
// here, with deliberately fake values that differ per theme and per role: a
// chart that read the wrong role, or the light value in dark mode, cannot
// pass by coincidence (the real category hues are the same in both themes).
const THEME_CSS = `
[data-theme="light"] {
  --category-1: #100001; --category-2: #100002; --category-other: #10000f;
  --panel: #1000a0; --chart-spending: #1000b0; --chart-income: #1000c0;
  --chart-grid: #1000d0; --control-border: #1000e0; --ink-secondary: #1000f0; --quiet: #100099;
}
:root { --font-sans: TestSans; }
.dark {
  --category-1: #200001; --category-2: #200002; --category-other: #20000f;
  --panel: #2000a0; --chart-spending: #2000b0; --chart-income: #2000c0;
  --chart-grid: #2000d0; --control-border: #2000e0; --ink-secondary: #2000f0; --quiet: #200099;
}`
let themeStyle: HTMLStyleElement
beforeEach(() => {
  themeStyle = document.createElement('style')
  themeStyle.textContent = THEME_CSS
  document.head.appendChild(themeStyle)
})
afterEach(() => {
  themeStyle.remove()
  localStorage.clear()
  document.documentElement.classList.remove('dark')
})
import type { ChartData } from '@/api/spending'

// --- Fixtures ----------------------------------------------------------
//
// DATA carries four split values across three periods. BANDS is what
// `bands(DATA.splits, DATA.cells)` would hand back for it: Hosting and
// Licences each earn their own band, and Tools + Training fold into one
// "Other (2)" band — built here by hand (rather than calling the real
// `bands()`) so the fixture stays a fixed, readable shape independent of
// palette.ts's fold threshold. The chart takes `bands` as an opaque prop;
// it must draw whatever it is given without asking how the fold happened.

const DATA: ChartData = {
  chart_id: 1,
  grain: 'month',
  split: 'kind',
  currency: 'USD',
  since: '2026-06-01',
  until: '2026-08-31',
  cells: [
    { period: '2026-06-01', split_value: 'hosting', total: '10.00', payments: 1 },
    { period: '2026-06-01', split_value: 'licences', total: '5.00', payments: 1 },
    { period: '2026-06-01', split_value: 'tools', total: '2.00', payments: 1 },
    { period: '2026-06-01', split_value: 'training', total: '1.00', payments: 1 },
    { period: '2026-07-01', split_value: 'hosting', total: '11.00', payments: 1 },
    { period: '2026-07-01', split_value: 'licences', total: '6.00', payments: 1 },
    { period: '2026-07-01', split_value: 'tools', total: '3.00', payments: 1 },
    { period: '2026-07-01', split_value: 'training', total: '1.50', payments: 1 },
    { period: '2026-08-01', split_value: 'hosting', total: '12.00', payments: 1 },
    { period: '2026-08-01', split_value: 'licences', total: '7.00', payments: 1 },
    { period: '2026-08-01', split_value: 'tools', total: '4.00', payments: 1 },
    { period: '2026-08-01', split_value: 'training', total: '2.00', payments: 1 },
  ],
  splits: [
    { value: 'hosting', label: 'Hosting', colour: null },
    { value: 'licences', label: 'Licences', colour: null },
    { value: 'tools', label: 'Tools', colour: null },
    { value: 'training', label: 'Training', colour: null },
  ],
  total: '64.50',
  payments: 12,
  documents: 12,
  footer: {
    netted_refunds: '0.00',
    refund_count: 0,
    excluded: [],
    unclassified: null,
    uncategorised: null,
    conflicting: null,
    undated: null,
    unaccounted: null,
    unconvertible: [],
  },
}

const BANDS: Band[] = [
  {
    value: 'hosting',
    label: 'Hosting',
    colour: SPLIT_PALETTE[0]!.colour,
    totalCents: 3300,
    members: [{ value: 'hosting', label: 'Hosting', colour: null }],
    isOther: false,
  },
  {
    value: 'licences',
    label: 'Licences',
    colour: SPLIT_PALETTE[1]!.colour,
    totalCents: 1800,
    members: [{ value: 'licences', label: 'Licences', colour: null }],
    isOther: false,
  },
  {
    value: OTHER_VALUE,
    label: 'Other (2)',
    colour: OTHER_COLOUR,
    totalCents: 1350,
    members: [
      { value: 'tools', label: 'Tools', colour: null },
      { value: 'training', label: 'Training', colour: null },
    ],
    isOther: true,
  },
]

// Same three periods as DATA, one bucket forced negative — a refund
// exceeding the payments in its bucket (spec §4.4 #2).
const WITH_NEGATIVE_CELL: ChartData = {
  ...DATA,
  cells: DATA.cells.map((c) =>
    c.period === '2026-08-01' && c.split_value === 'hosting' ? { ...c, total: '-3.00' } : c,
  ),
}

// Every segment negative for one period — a bucket that is entirely a
// refund, so the whole stack's net is negative (distinct from
// WITH_NEGATIVE_CELL, which mixes one negative segment into an otherwise
// positive stack).
const ALL_NEGATIVE_PERIOD: ChartData = {
  ...DATA,
  cells: DATA.cells.map((c) => (c.period === '2026-06-01' ? { ...c, total: `-${c.total}` } : c)),
}

const UNSPLIT: ChartData = {
  ...DATA,
  split: null,
  splits: [],
  cells: [
    { period: '2026-06-01', split_value: null, total: '18.00', payments: 4 },
    { period: '2026-07-01', split_value: null, total: '21.50', payments: 4 },
    { period: '2026-08-01', split_value: null, total: '25.00', payments: 4 },
  ],
}

function mountChart(
  data: ChartData = DATA,
  hidden?: Set<string | null | symbol>,
  bands: Band[] = data.splits.length === 0 ? [] : BANDS,
) {
  return mount(SpendingChart, { props: { data, bands, hidden } })
}

interface BarProps {
  plugins?: unknown[]
  data: { labels: string[]; datasets: BarDataset[] }
  options: {
    onClick?: (event: unknown, elements: { datasetIndex: number; index: number }[]) => void
    scales: { x: { type: string; stacked: boolean }; y: { stacked: boolean; beginAtZero: boolean } }
  }
}
interface FullBorderRadius {
  topLeft: number
  topRight: number
  bottomLeft: number
  bottomRight: number
}
interface BarDataset {
  label: string
  data: number[]
  backgroundColor: string | string[]
  borderWidth: number
  borderColor: string
  maxBarThickness: number
  borderRadius: (ctx: { dataIndex: number }) => FullBorderRadius
}

function barProps(wrapper: VueWrapper): BarProps {
  return wrapper.findComponent({ name: 'Bar' }).props() as unknown as BarProps
}
function chartDataOf(wrapper: VueWrapper) {
  return barProps(wrapper).data
}
function optionsOf(wrapper: VueWrapper) {
  return barProps(wrapper).options
}
function clickBar(wrapper: VueWrapper, element: { datasetIndex: number; index: number }): void {
  optionsOf(wrapper).onClick?.({}, [element])
}

describe('SpendingChart', () => {
  it('stacks both axes so the stack height is the total', () => {
    const options = optionsOf(mountChart())
    expect(options.scales.x.stacked).toBe(true)
    expect(options.scales.y.stacked).toBe(true)
  })

  // A refund exceeding its bucket's payments draws below the baseline; an axis
  // that starts elsewhere hides the sign.
  it('always includes zero on the y axis', () => {
    const options = optionsOf(mountChart(WITH_NEGATIVE_CELL))
    expect(options.scales.y.beginAtZero).toBe(true)
  })

  it('uses a category x axis, so every period is the same width', () => {
    // A TimeScale would size bars by date distance. The labels are the periods.
    const options = optionsOf(mountChart())
    expect(options.scales.x.type).toBe('category')
    expect(chartDataOf(mountChart()).labels).toEqual(['2026-06-01', '2026-07-01', '2026-08-01'])
  })

  // The chart takes `bands` as a prop and must not re-derive a colour: the
  // assignment (fold, de-collision, stored overrides) belongs to palette.ts.
  it('draws one dataset per band, in band order, with the band colour', () => {
    const bands = BANDS // built by the fixture to match bands(splits, cells)'s shape
    const datasets = chartDataOf(mountChart()).datasets
    expect(datasets.map((d) => d.label)).toEqual(bands.map((b) => b.label))
    // Each band's role, resolved for the light theme: category-1, category-2, then Other.
    expect(datasets.map((d) => d.backgroundColor)).toEqual(['#100001', '#100002', '#10000f'])
  })

  // useDark reads the `vueuse-color-scheme` key at setup, so seeding it before
  // mount is how a spec forces the dark arm (ThemeToggle.spec.ts does the same).
  it('paints every role in its dark value when the dark theme is on', () => {
    localStorage.setItem('vueuse-color-scheme', 'dark')
    const datasets = chartDataOf(mountChart()).datasets
    expect(datasets.map((d) => d.backgroundColor)).toEqual(['#200001', '#200002', '#20000f'])
    expect(datasets.every((d) => d.borderColor === '#2000a0')).toBe(true)
  })

  it('paints an owner\'s stored hex from outside the palette exactly as stored', () => {
    const owned: Band[] = [{ ...BANDS[0]!, colour: '#876708' }, ...BANDS.slice(1)]
    const datasets = chartDataOf(mountChart(DATA, undefined, owned)).datasets
    expect(datasets[0]!.backgroundColor).toBe('#876708')
  })

  // Nothing in the app is smaller than `type-small` (12.5px), the axis
  // included, and a canvas does not inherit the page's font.
  it('draws gridlines in chart-grid, the zero line in control-border, ticks in ink-secondary at type-small', () => {
    const options = optionsOf(mountChart()) as unknown as {
      scales: {
        y: { grid: { color: (ctx: { tick?: { value: number } }) => string }; ticks: { color: string; font: { size: number } } }
        x: { ticks: { color: string; font: { size: number } } }
      }
    }
    expect(options.scales.y.grid.color({ tick: { value: 10 } })).toBe('#1000d0')
    expect(options.scales.y.grid.color({ tick: { value: 0 } })).toBe('#1000e0')
    const tick = { color: '#1000f0', font: { family: 'TestSans', size: 12.5 } }
    expect(options.scales.y.ticks).toMatchObject(tick)
    expect(options.scales.x.ticks).toMatchObject(tick)
  })

  // The 2px surface gap is the separator; a border around a mark is not.
  it('separates touching segments with a surface-coloured gap, not a stroke', () => {
    const datasets = chartDataOf(mountChart()).datasets
    expect(datasets.every((d) => d.borderWidth === 2)).toBe(true)
    // `panel`, the card's own fill, resolved for the light theme.
    expect(datasets.every((d) => d.borderColor === '#1000a0')).toBe(true)
  })

  // About two thirds of the slot, at most 120px (ChartStyle card): thick on a
  // wide card with few periods, thin enough to fit many.
  it('fills about two thirds of each period\'s slot, capped so a two-period chart does not draw slabs', () => {
    const dataset = chartDataOf(mountChart()).datasets[0]! as BarDataset & { barPercentage: number; categoryPercentage: number }
    expect(dataset.categoryPercentage * dataset.barPercentage).toBeCloseTo(0.63)
    expect(dataset.maxBarThickness).toBe(120)
  })

  it('emits the cell that was clicked, with its raw split value', () => {
    const wrapper = mountChart()
    clickBar(wrapper, { datasetIndex: 0, index: 2 })
    expect(wrapper.emitted('cell')![0]).toEqual(['2026-08-01', 'hosting'])
  })

  it('emits the Other symbol for the folded band, never a fake split value', () => {
    const wrapper = mountChart()
    clickBar(wrapper, { datasetIndex: 2, index: 2 })
    expect(wrapper.emitted('cell')![0]).toEqual(['2026-08-01', OTHER_VALUE])
  })

  // Isolation is a display filter and must not change the assignment.
  it('hides a band without recolouring the survivors', () => {
    const before = chartDataOf(mountChart(DATA)).datasets
    const after = chartDataOf(mountChart(DATA, new Set(['licences']))).datasets
    expect(after.map((d) => d.label)).toEqual(['Hosting', 'Other (2)'])
    // Every survivor keeps the exact colour it had before the filter.
    for (const dataset of after) {
      const was = before.find((d) => d.label === dataset.label)!
      expect(dataset.backgroundColor).toBe(was.backgroundColor)
    }
  })

  // The unsplit-total fallback must key on `bands.length === 0`, never
  // `visibleBands.length === 0` — the latter is ALSO true once every band
  // has been hidden via the legend, and would redraw the chart as a single
  // series at the grand total (summing the hidden cells right back in)
  // directly under a selection line claiming everything is hidden. An empty
  // chart is the honest answer to "everything is hidden".
  it('draws zero datasets when every band is hidden — never the unsplit grand total', () => {
    const allHidden = new Set(BANDS.map((b) => b.value))
    const datasets = chartDataOf(mountChart(DATA, allHidden)).datasets
    expect(datasets).toEqual([])
  })

  it('renders a single unsplit series with no legend datasets to name', () => {
    // `bands()` returns [] for an unsplit chart, so the chart draws one series
    // of spending, bar by bar, and the legend renders nothing.
    const datasets = chartDataOf(mountChart(UNSPLIT)).datasets
    expect(datasets).toHaveLength(1)
    expect(datasets[0]!.backgroundColor).toEqual(['#1000b0', '#1000b0', '#1000b0'])
  })

  // Design system, Charts: a refund below the zero line is money in.
  it('draws an unsplit period whose refunds exceed its payments in the income colour', () => {
    const refunded: ChartData = {
      ...UNSPLIT,
      cells: UNSPLIT.cells.map((c) => (c.period === '2026-07-01' ? { ...c, total: '-4.00' } : c)),
    }
    const datasets = chartDataOf(mountChart(refunded)).datasets
    expect(datasets[0]!.backgroundColor).toEqual(['#1000b0', '#1000c0', '#1000b0'])
  })

  // Extra coverage beyond the brief's pinned assertions ----------------------

  it('sums an Other band across every folded member, per period', () => {
    // 2026-08-01: tools 4.00 + training 2.00 = 6.00.
    const datasets = chartDataOf(mountChart()).datasets
    expect(datasets[2]!.data[2]).toBeCloseTo(6.0)
  })

  it('emits null for the unsplit series — there is no split value to fake', () => {
    const wrapper = mountChart(UNSPLIT)
    clickBar(wrapper, { datasetIndex: 0, index: 0 })
    expect(wrapper.emitted('cell')![0]).toEqual(['2026-06-01', null])
  })

  it('formats the tooltip as the band label and formatMoney(cell.total, currency)', () => {
    const options = optionsOf(mountChart())
    const label = (
      options as unknown as {
        plugins: { tooltip: { callbacks: { label: (ctx: { datasetIndex: number; dataIndex: number }) => string } } }
      }
    ).plugins.tooltip.callbacks.label
    // Hosting (dataset 0), 2026-08-01 (index 2): total 12.00 USD.
    expect(label({ datasetIndex: 0, dataIndex: 2 })).toBe('Hosting: USD 12.00')
  })

  // The tooltip TITLE (the period, drawn above the label lines) is never
  // overridden — `plugins.tooltip.callbacks` carries no `title` key at all,
  // so Chart.js's own default applies: it renders the x-axis category label
  // for the hovered bar, i.e. `chartData.labels[dataIndex]`. That default is
  // only correct because `labels` is `periods` (asserted above) — this test
  // pins both halves of that reliance: no override exists, and the data the
  // default would read from is the period string, not something else.
  it('relies on Chart.js\'s own default tooltip title — never overrides it', () => {
    const wrapper = mountChart()
    const options = optionsOf(wrapper) as unknown as {
      plugins: { tooltip: { callbacks: Record<string, unknown> } }
    }
    expect(options.plugins.tooltip.callbacks.title).toBeUndefined()
    expect(chartDataOf(wrapper).labels).toEqual(['2026-06-01', '2026-07-01', '2026-08-01'])
  })

  it('writes y ticks as money in the chart\'s display currency', () => {
    const options = optionsOf(mountChart({ ...DATA, currency: 'EUR' })) as unknown as {
      scales: { y: { ticks: { callback: (value: number | string) => string } } }
    }
    expect(options.scales.y.ticks.callback(200)).toBe('EUR 200')
    expect(options.scales.y.ticks.callback(-100)).toBe('\u2212EUR 100')
  })

  // Hovering any segment shows the whole bar: every series with money in that
  // period, top segment first so the list reads like the bar, then the total.
  // Clicking still drills into the one segment under the pointer.
  describe('the tooltip', () => {
    interface TooltipItem { datasetIndex: number; dataIndex: number; raw: number }
    type TooltipOptions = {
      mode: string
      intersect: boolean
      itemSort: (a: TooltipItem, b: TooltipItem) => number
      filter: (item: TooltipItem) => boolean
      callbacks: { footer: (items: TooltipItem[]) => string }
      titleFont: object
      bodyFont: object
      footerFont: object
    }
    const tooltipOf = (w: VueWrapper) =>
      (optionsOf(w) as unknown as { plugins: { tooltip: TooltipOptions } }).plugins.tooltip

    it('shows every series in the hovered bar, not only the segment under the pointer', () => {
      const tooltip = tooltipOf(mountChart())
      expect(tooltip.mode).toBe('index')
      expect(tooltip.intersect).toBe(true)
    })

    it('lists the top segment first, mirroring the stack', () => {
      const items = [0, 1, 2].map((datasetIndex) => ({ datasetIndex, dataIndex: 0, raw: 1 }))
      expect([...items].sort(tooltipOf(mountChart()).itemSort).map((i) => i.datasetIndex)).toEqual([2, 1, 0])
    })

    it('leaves out a series with nothing in that period', () => {
      expect(tooltipOf(mountChart()).filter({ datasetIndex: 0, dataIndex: 0, raw: 0 })).toBe(false)
      expect(tooltipOf(mountChart()).filter({ datasetIndex: 0, dataIndex: 0, raw: 10 })).toBe(true)
    })

    it('ends with the bar\'s total across the visible bands', () => {
      const footer = tooltipOf(mountChart(DATA)).callbacks.footer
      // June: hosting 10 + licences 5 + Other (tools 2 + training 1) = 18.00.
      expect(footer([{ datasetIndex: 0, dataIndex: 0, raw: 10 }])).toBe('Total: USD 18.00')
      // Hiding licences takes it out of the total, as it is out of the bar.
      const hidden = tooltipOf(mountChart(DATA, new Set(['licences']))).callbacks.footer
      expect(hidden([{ datasetIndex: 0, dataIndex: 0, raw: 10 }])).toBe('Total: USD 13.00')
    })

    // Chart.js's default is 12px Helvetica; the tooltip is where figures are
    // read, so it takes `type-body`, the period and total at 600.
    it('writes in type-body in the app\'s font, the period and total at 600', () => {
      const tooltip = tooltipOf(mountChart())
      const family = 'TestSans'
      expect(tooltip.titleFont).toEqual({ family, size: 14, lineHeight: 1.5, weight: 600 })
      expect(tooltip.bodyFont).toEqual({ family, size: 14, lineHeight: 1.5 })
      expect(tooltip.footerFont).toEqual({ family, size: 14, lineHeight: 1.5, weight: 600 })
    })

    it('needs no total line for an unsplit chart', () => {
      expect(tooltipOf(mountChart(UNSPLIT)).callbacks.footer([{ datasetIndex: 0, dataIndex: 0, raw: 18 }])).toBe('')
    })
  })

  it('drops y-axis ticks and shortens x labels when compact, without touching the data', () => {
    const wrapper = mount(SpendingChart, { props: { data: DATA, bands: BANDS, compact: true } })
    const options = optionsOf(wrapper) as unknown as {
      scales: { y: { ticks: { display?: boolean } }; x: { ticks: { callback?: (v: unknown, i: number) => string } } }
    }
    expect(options.scales.y.ticks.display).toBe(false)
    expect(options.scales.x.ticks.callback?.(undefined, 2)).toBe('2026-08')
    // The underlying series is unchanged.
    expect(chartDataOf(wrapper).datasets).toHaveLength(3)
  })

  it('leaves every array index alone when a mid-stream period has no cell for a band', () => {
    // A split value with no cells in a given period is a real zero, not an
    // absent bar — the stack must still have three entries.
    const sparse: ChartData = {
      ...DATA,
      cells: DATA.cells.filter((c) => !(c.period === '2026-07-01' && c.split_value === 'licences')),
    }
    const datasets = chartDataOf(mountChart(sparse)).datasets
    expect(datasets[1]!.data).toEqual([5, 0, 7]) // Licences: Jun, (missing) Jul, Aug
  })

  // --- borderRadius: only the outermost stack segment rounds -----------------
  //
  // The mock hands us the real scriptable `borderRadius` function on each
  // dataset object, so it is exercised directly — `datasets[i].borderRadius`
  // is exactly what Chart.js itself would call per bar segment, per render.

  const SQUARE = { topLeft: 0, topRight: 0, bottomLeft: 0, bottomRight: 0 }
  const TOP_ROUNDED = { topLeft: 4, topRight: 4, bottomLeft: 0, bottomRight: 0 }
  const BOTTOM_ROUNDED = { topLeft: 0, topRight: 0, bottomLeft: 4, bottomRight: 4 }

  it('rounds only the topmost segment of an all-positive stack', () => {
    // 2026-06-01 (index 0): hosting 10.00, licences 5.00, Other 3.00 — all
    // positive, so the topmost (last, in stack order) is Other.
    const datasets = chartDataOf(mountChart()).datasets
    expect(datasets[0]!.borderRadius({ dataIndex: 0 })).toEqual(SQUARE)
    expect(datasets[1]!.borderRadius({ dataIndex: 0 })).toEqual(SQUARE)
    expect(datasets[2]!.borderRadius({ dataIndex: 0 })).toEqual(TOP_ROUNDED)
  })

  it('rounds the bottom of the outermost negative segment when the stack net is negative', () => {
    // 2026-06-01 (index 0) under ALL_NEGATIVE_PERIOD: hosting -10.00,
    // licences -5.00, Other -3.00 — every segment negative, so the outer
    // (most negative, last in stack order) is Other, rounded at the bottom.
    const datasets = chartDataOf(mountChart(ALL_NEGATIVE_PERIOD)).datasets
    expect(datasets[0]!.borderRadius({ dataIndex: 0 })).toEqual(SQUARE)
    expect(datasets[1]!.borderRadius({ dataIndex: 0 })).toEqual(SQUARE)
    expect(datasets[2]!.borderRadius({ dataIndex: 0 })).toEqual(BOTTOM_ROUNDED)
  })

  it('rounds the positive arm at its top and the negative arm at its bottom, independently, in a mixed stack', () => {
    // 2026-08-01 (index 2) under WITH_NEGATIVE_CELL: hosting -3.00 (the only
    // negative segment — bottom-rounded), licences 7.00 (interior positive —
    // square), Other 6.00 (the last positive — top-rounded).
    const datasets = chartDataOf(mountChart(WITH_NEGATIVE_CELL)).datasets
    expect(datasets[0]!.borderRadius({ dataIndex: 2 })).toEqual(BOTTOM_ROUNDED)
    expect(datasets[1]!.borderRadius({ dataIndex: 2 })).toEqual(SQUARE)
    expect(datasets[2]!.borderRadius({ dataIndex: 2 })).toEqual(TOP_ROUNDED)
  })

  it('keeps a zero-valued segment square and never picks it as the outermost', () => {
    // 2026-07-01 (index 1) with the Licences cell removed: hosting 11.00,
    // licences 0 (absent), Other 4.50 — the zero segment stays square, and
    // Other (still the last positive) keeps the top rounding regardless.
    const sparse: ChartData = {
      ...DATA,
      cells: DATA.cells.filter((c) => !(c.period === '2026-07-01' && c.split_value === 'licences')),
    }
    const datasets = chartDataOf(mountChart(sparse)).datasets
    expect(datasets[1]!.borderRadius({ dataIndex: 1 })).toEqual(SQUARE)
    expect(datasets[0]!.borderRadius({ dataIndex: 1 })).toEqual(SQUARE)
    expect(datasets[2]!.borderRadius({ dataIndex: 1 })).toEqual(TOP_ROUNDED)
  })
})

// A month with no documents has no cell (the API groups by period), so the
// axis would skip it and July would touch September. The chart fills the gap
// and marks it with a 3px `quiet` stub (ChartStyle card).
describe('SpendingChart: a period with no documents', () => {
  const GAP: ChartData = {
    ...DATA,
    cells: DATA.cells.filter((c) => c.period !== '2026-07-01'),
  }

  interface StubChart {
    ctx: { fillStyle: string; beginPath: () => void; roundRect: (...a: number[]) => void; fill: () => void; save: () => void; restore: () => void }
    chartArea: { bottom: number }
    scales: { x: { width: number; getPixelForValue: (i: number) => number }; y: { getPixelForValue: (v: number) => number } }
    data: { labels: string[] }
  }
  function drawStubs(wrapper: VueWrapper): number[][] {
    const plugins = barProps(wrapper).plugins as { id: string; afterDatasetsDraw: (chart: StubChart) => void }[]
    const plugin = plugins.find((p) => p.id === 'emptyPeriodStub')!
    const rects: number[][] = []
    let fill = ''
    const chart: StubChart = {
      ctx: {
        set fillStyle(v: string) { fill = v },
        get fillStyle() { return fill },
        beginPath: () => {},
        roundRect: (...a: number[]) => rects.push([...a]),
        fill: () => {},
        save: () => {},
        restore: () => {},
      },
      chartArea: { bottom: 200 },
      scales: { x: { width: 300, getPixelForValue: (i) => 50 + i * 100 }, y: { getPixelForValue: () => 150 } },
      data: { labels: chartDataOf(wrapper).labels },
    }
    plugin.afterDatasetsDraw(chart)
    expect(rects.length === 0 || fill === '#100099').toBe(true)
    return rects
  }

  it('keeps the empty month on the axis, with zero in every band', () => {
    const { labels, datasets } = chartDataOf(mountChart(GAP))
    expect(labels).toEqual(['2026-06-01', '2026-07-01', '2026-08-01'])
    expect(datasets.map((d) => d.data[1])).toEqual([0, 0, 0])
  })

  it('draws one 3px quiet stub at the zero line, as wide as a bar\'s colour, centred on the empty month', () => {
    const rects = drawStubs(mountChart(GAP))
    // 300px / 3 periods * 0.63 = 63px, under the 120px cap. A bar paints its
    // 2px surface border inside that width on both sides, so its colour is
    // 59px wide, and the stub matches the colour rather than the outline.
    expect(rects).toEqual([[150 - 29.5, 150 - 3, 59, 3, 1.5]])
  })

  it('draws no stub when every month has documents', () => {
    expect(drawStubs(mountChart(DATA))).toEqual([])
  })

  it('still draws the stub when every band is hidden by the legend', () => {
    const allHidden = new Set(BANDS.map((b) => b.value))
    expect(drawStubs(mountChart(GAP, allHidden))).toHaveLength(1)
  })

  it('opens nothing when the empty month is clicked', () => {
    const wrapper = mountChart(GAP)
    clickBar(wrapper, { datasetIndex: 0, index: 1 })
    expect(wrapper.emitted('cell')).toBeUndefined()
  })

  it('shows no tooltip for the empty month', () => {
    const options = optionsOf(mountChart(GAP)) as unknown as {
      plugins: { tooltip: { filter: (item: { dataIndex: number }) => boolean } }
    }
    expect(options.plugins.tooltip.filter({ dataIndex: 1 })).toBe(false)
    expect(options.plugins.tooltip.filter({ dataIndex: 0 })).toBe(true)
  })

  it('fills weeks at the week grain', () => {
    const weekly: ChartData = {
      ...UNSPLIT,
      grain: 'week',
      cells: [
        { period: '2026-06-01', split_value: null, total: '5.00', payments: 1 },
        { period: '2026-06-22', split_value: null, total: '7.00', payments: 1 },
      ],
    }
    expect(chartDataOf(mountChart(weekly)).labels).toEqual(['2026-06-01', '2026-06-08', '2026-06-15', '2026-06-22'])
    expect(drawStubs(mountChart(weekly))).toHaveLength(2)
  })
})
