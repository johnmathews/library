import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { nextTick } from 'vue'

// Capture router navigations.
const push = vi.fn()
vi.mock('vue-router', () => ({
  useRouter: () => ({ push }),
  RouterLink: {
    props: ['to'],
    template: '<a :href="to"><slot /></a>',
  },
}))

// The tile pulls in chart.js; stub the renderer + its date adapter. Capture the
// options object so tests can invoke Chart.js callbacks (onClick / onHover /
// the grouped external tooltip handler) directly.
const capture = { options: null as unknown }
vi.mock('vue-chartjs', () => ({
  Bar: {
    name: 'Bar',
    props: ['data', 'options'],
    template: '<canvas />',
    mounted() {
      capture.options = (this as unknown as { options: unknown }).options
    },
  },
}))
vi.mock('chartjs-adapter-date-fns', () => ({}))

// Runtime API imports used by the editable controls; ids kept real for hrefs.
vi.mock('@/api/documents', () => ({
  addSeriesMember: vi.fn(),
  removeSeriesMember: vi.fn(),
  listDocuments: vi.fn(),
  updateSeriesMeta: vi.fn(),
  updateAuthoredSeries: vi.fn(),
  addAuthoredMember: vi.fn(),
  removeAuthoredMember: vi.fn(),
  fetchAuthoredSuggestions: vi.fn(),
  acceptAuthoredSuggestion: vi.fn(),
  dismissAuthoredSuggestion: vi.fn(),
  fetchAuthoredOddOnesOut: vi.fn(),
  authoredSeriesId: (id: number) => `a-${id}`,
  seriesId: (s: { sender_id: number; kind_id: number; currency: string | null }) =>
    `${s.sender_id}-${s.kind_id}-${s.currency ?? 'none'}`,
}))

import SeriesChartTile from '../SeriesChartTile.vue'
import type { DocumentSeries } from '@/api/documents'

const series: DocumentSeries = {
  status: 'ok',
  sender: 'Vattenfall',
  kind: 'utility-bill',
  sender_id: 7,
  kind_id: 2,
  currency: 'EUR',
  other_currencies: [],
  cadence: 'monthly',
  count: 1,
  document_ids: [1],
  points: [{ date: '2025-01-03', amount: '100.00', document_id: 1, title: 'Jan' }],
}

function mountTile(props: Record<string, unknown>) {
  return mount(SeriesChartTile, { props: { series, ...props } })
}

describe('SeriesChartTile — click to open (W3)', () => {
  beforeEach(() => push.mockClear())

  it('links the heading to the single-chart page when detailLink is set', () => {
    const wrapper = mountTile({ detailLink: true })
    const link = wrapper.find('[data-testid="series-heading-link"]')
    expect(link.exists()).toBe(true)
    expect(link.attributes('href')).toBe('/charts/7-2-EUR')
  })

  it('navigates when the chart area is clicked', async () => {
    const wrapper = mountTile({ detailLink: true })
    const area = wrapper.find('[data-testid="series-chart-area"]')
    expect(area.attributes('role')).toBe('link')
    await area.trigger('click')
    expect(push).toHaveBeenCalledWith('/charts/7-2-EUR')
  })

  it('does not navigate from the Edit button', async () => {
    const wrapper = mountTile({ detailLink: true, editable: true })
    await wrapper.find('[data-testid="series-meta-edit"]').trigger('click')
    expect(push).not.toHaveBeenCalled()
    // Edit opens the inline form rather than navigating.
    expect(wrapper.find('[data-testid="series-meta-form"]').exists()).toBe(true)
  })

  it('is inert without detailLink (detail page / trend embed)', async () => {
    const wrapper = mountTile({})
    expect(wrapper.find('[data-testid="series-heading-link"]').exists()).toBe(false)
    const area = wrapper.find('[data-testid="series-chart-area"]')
    expect(area.attributes('role')).toBeUndefined()
    await area.trigger('click')
    expect(push).not.toHaveBeenCalled()
  })
})

// A multi-document series that spans one month (so grouped mode rolls them into
// a single bucket) — used by the clickable-source tests below (W2).
const multiSeries: DocumentSeries = {
  status: 'ok',
  sender: 'Vattenfall',
  kind: 'utility-bill',
  sender_id: 7,
  kind_id: 2,
  currency: 'EUR',
  other_currencies: [],
  cadence: 'monthly',
  count: 3,
  document_ids: [11, 12, 13],
  points: [
    { date: '2025-01-03', amount: '100.00', document_id: 11, title: 'First' },
    { date: '2025-01-10', amount: '50.00', document_id: 12, title: 'Second' },
    { date: '2025-01-20', amount: '30.00', document_id: 13, title: null },
  ],
}

interface CapturedOptions {
  onClick?: (event: unknown, elements: { index: number }[]) => void
  onHover?: (event: unknown, elements: { index: number }[]) => void
  plugins: {
    tooltip: {
      enabled?: boolean
      external?: (ctx: {
        chart: unknown
        tooltip: { opacity: number; caretX: number; caretY: number; dataPoints?: { dataIndex: number }[] }
      }) => void
    }
  }
}

describe('SeriesChartTile — clickable bar source, UNGROUPED (W2)', () => {
  beforeEach(() => {
    push.mockClear()
    capture.options = null
  })

  it('navigates to the clicked bar’s document', () => {
    mount(SeriesChartTile, { props: { series: multiSeries } }) // no grouping -> ungrouped
    const opts = capture.options as CapturedOptions
    expect(typeof opts.onClick).toBe('function')
    // Chart.js hands the active element as { index } (the dataIndex).
    opts.onClick!({ native: { stopPropagation: vi.fn() } }, [{ index: 1 }])
    expect(push).toHaveBeenCalledWith('/documents/12')
  })

  it('does nothing when the click hits no bar', () => {
    mount(SeriesChartTile, { props: { series: multiSeries } })
    const opts = capture.options as CapturedOptions
    opts.onClick!({ native: { stopPropagation: vi.fn() } }, [])
    expect(push).not.toHaveBeenCalled()
  })

  it('sets a pointer cursor over a bar and resets it off a bar', () => {
    mount(SeriesChartTile, { props: { series: multiSeries } })
    const opts = capture.options as CapturedOptions
    const target = { style: { cursor: '' } }
    opts.onHover!({ native: { target } }, [{ index: 0 }])
    expect(target.style.cursor).toBe('pointer')
    opts.onHover!({ native: { target } }, [])
    expect(target.style.cursor).toBe('default')
  })

  it('has no per-bar onClick/onHover in grouped mode', () => {
    mount(SeriesChartTile, { props: { series: multiSeries, grouping: 'month' } })
    const opts = capture.options as CapturedOptions
    expect(opts.onClick).toBeUndefined()
    expect(opts.onHover).toBeUndefined()
  })
})

describe('SeriesChartTile — clickable sticky tooltip, GROUPED (W2)', () => {
  beforeEach(() => {
    push.mockClear()
    capture.options = null
  })

  function showTooltip(wrapper: ReturnType<typeof mount>, dataIndex = 0) {
    const opts = capture.options as CapturedOptions
    expect(opts.plugins.tooltip.enabled).toBe(false)
    expect(typeof opts.plugins.tooltip.external).toBe('function')
    opts.plugins.tooltip.external!({
      chart: {},
      tooltip: { opacity: 1, caretX: 40, caretY: 20, dataPoints: [{ dataIndex }] },
    })
  }

  it('renders a clickable link per bucket document with the right hrefs', async () => {
    const wrapper = mount(SeriesChartTile, {
      props: { series: multiSeries, grouping: 'month' },
    })
    // No overlay until the external tooltip handler fires.
    expect(wrapper.find('[data-testid="chart-tooltip"]').exists()).toBe(false)

    showTooltip(wrapper)
    await nextTick()

    const tip = wrapper.find('[data-testid="chart-tooltip"]')
    expect(tip.exists()).toBe(true)
    // Header keeps the existing bucket total + document count.
    expect(wrapper.find('[data-testid="chart-tooltip-header"]').text()).toContain('3 documents')
    // One link per contributing document, each pointing at its source document.
    const links = wrapper.findAll('[data-testid="chart-tooltip-doc-link"]')
    expect(links).toHaveLength(3)
    expect(links.map((l) => l.attributes('href'))).toEqual([
      '/documents/11',
      '/documents/12',
      '/documents/13',
    ])
  })

  it('stays open while the pointer is over it, then closes on leave', async () => {
    const wrapper = mount(SeriesChartTile, {
      props: { series: multiSeries, grouping: 'month' },
    })
    showTooltip(wrapper)
    await nextTick()
    const tip = wrapper.find('[data-testid="chart-tooltip"]')

    // Pointer leaves the bar: Chart.js reports opacity 0 (schedules a hide).
    const opts = capture.options as CapturedOptions
    opts.plugins.tooltip.external!({ chart: {}, tooltip: { opacity: 0, caretX: 0, caretY: 0 } })
    // Pointer moves onto the tooltip -> cancels the pending hide.
    await tip.trigger('mouseenter')
    await flushPromises()
    expect(wrapper.find('[data-testid="chart-tooltip"]').exists()).toBe(true)

    // Leaving the tooltip closes it immediately.
    await tip.trigger('mouseleave')
    expect(wrapper.find('[data-testid="chart-tooltip"]').exists()).toBe(false)
  })
})
