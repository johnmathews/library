import { describe, it, expect, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'

vi.mock('vue-chartjs', () => ({
  Bar: { name: 'Bar', props: ['data', 'options'], template: '<canvas />' },
}))

import SpendingChart from '../SpendingChart.vue'
import { OTHER_VALUE, OTHER_COLOUR, type Band } from '@/spending/palette'
import { SPLIT_PALETTE } from '@/utils/splitPalette'
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
    undated: null,
    unaccounted: null,
    unconvertible: [],
  },
}

const BANDS: Band[] = [
  {
    value: 'hosting',
    label: 'Hosting',
    light: SPLIT_PALETTE[0]!.light,
    dark: SPLIT_PALETTE[0]!.dark,
    totalCents: 3300,
    members: [{ value: 'hosting', label: 'Hosting', colour: null }],
    isOther: false,
  },
  {
    value: 'licences',
    label: 'Licences',
    light: SPLIT_PALETTE[1]!.light,
    dark: SPLIT_PALETTE[1]!.dark,
    totalCents: 1800,
    members: [{ value: 'licences', label: 'Licences', colour: null }],
    isOther: false,
  },
  {
    value: OTHER_VALUE,
    label: 'Other (2)',
    light: OTHER_COLOUR.light,
    dark: OTHER_COLOUR.dark,
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
  backgroundColor: string
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
    expect(datasets.map((d) => d.backgroundColor)).toEqual(bands.map((b) => b.light))
  })

  // The 2px surface gap is the separator; a border around a mark is not.
  it('separates touching segments with a surface-coloured gap, not a stroke', () => {
    const datasets = chartDataOf(mountChart()).datasets
    expect(datasets.every((d) => d.borderWidth === 2)).toBe(true)
    expect(datasets.every((d) => d.borderColor === '#ffffff')).toBe(true)
  })

  it('caps bar thickness so a two-period chart does not draw slabs', () => {
    expect(chartDataOf(mountChart()).datasets[0]!.maxBarThickness).toBe(24)
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

  it('renders a single unsplit series with no legend datasets to name', () => {
    // `bands()` returns [] for an unsplit chart, so the chart draws one series
    // in the first palette slot and the legend renders nothing.
    const datasets = chartDataOf(mountChart(UNSPLIT)).datasets
    expect(datasets).toHaveLength(1)
    expect(datasets[0]!.backgroundColor).toBe(SPLIT_PALETTE[0]!.light)
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
