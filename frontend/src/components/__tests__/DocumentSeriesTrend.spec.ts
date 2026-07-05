import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('@/api/documents', () => ({ fetchDocumentSeries: vi.fn() }))

import { fetchDocumentSeries } from '@/api/documents'
import DocumentSeriesTrend from '../DocumentSeriesTrend.vue'

// Stub the presentational tile: this spec covers the wrapper's fetch/delegate
// behaviour plus the control-driven timeframe/grouping wiring (the tile has its
// own spec). Expose the props the wrapper feeds it so we can assert on them.
const TileStub = {
  name: 'SeriesChartTile',
  props: ['series', 'highlightDocumentId', 'axisMin', 'axisMax', 'grouping'],
  template:
    '<div data-testid="tile-stub" :data-points="series.points.length" :data-grouping="grouping">{{ series.sender }}|{{ highlightDocumentId }}</div>',
}

function mountTrend(documentId: number) {
  return mount(DocumentSeriesTrend, {
    props: { documentId },
    global: { stubs: { SeriesChartTile: TileStub } },
  })
}

// A date guaranteed to sit inside every bounded preset window (max is always
// "today"), so default 12m filtering keeps it.
function recentIso(daysAgo = 1): string {
  const d = new Date()
  d.setDate(d.getDate() - daysAgo)
  return d.toISOString().slice(0, 10)
}

// A date well outside the default 12-month window but inside "all".
function oldIso(): string {
  const d = new Date()
  d.setFullYear(d.getFullYear() - 2)
  return d.toISOString().slice(0, 10)
}

function okSeries(points: { date: string; amount: string; document_id: number }[]) {
  return {
    status: 'ok',
    sender: 'Vattenfall',
    kind: 'utility-bill',
    currency: 'EUR',
    other_currencies: [],
    cadence: 'monthly',
    count: points.length,
    document_ids: points.map((p) => p.document_id),
    points,
  } as never
}

describe('DocumentSeriesTrend', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Persisted controls use doc-specific keys; reset so every test starts at
    // the shared defaults (timeframe '12m', grouping 'month').
    localStorage.clear()
  })

  it('renders the tile and passes the series + highlight id when status ok', async () => {
    vi.mocked(fetchDocumentSeries).mockResolvedValue(
      okSeries([{ date: recentIso(), amount: '100.00', document_id: 1 }]),
    )
    const wrapper = mountTrend(3)
    await flushPromises()
    const tile = wrapper.find('[data-testid="tile-stub"]')
    expect(tile.exists()).toBe(true)
    expect(tile.text()).toBe('Vattenfall|3') // highlightDocumentId == documentId
  })

  it('defaults to timeframe 12m and grouping month', async () => {
    vi.mocked(fetchDocumentSeries).mockResolvedValue(
      okSeries([{ date: recentIso(), amount: '100.00', document_id: 1 }]),
    )
    const wrapper = mountTrend(3)
    await flushPromises()
    const timeframe = wrapper.find('[data-testid="charts-timeframe"]')
      .element as HTMLSelectElement
    const grouping = wrapper.find('[data-testid="charts-grouping"]')
      .element as HTMLSelectElement
    expect(timeframe.value).toBe('12m')
    expect(grouping.value).toBe('month')
    // Grouping is forwarded to the tile.
    expect(wrapper.find('[data-testid="tile-stub"]').attributes('data-grouping')).toBe('month')
  })

  it('exposes the controls container under a stable testid', async () => {
    vi.mocked(fetchDocumentSeries).mockResolvedValue(
      okSeries([{ date: recentIso(), amount: '100.00', document_id: 1 }]),
    )
    const wrapper = mountTrend(3)
    await flushPromises()
    expect(wrapper.find('[data-testid="doc-series-controls"]').exists()).toBe(true)
  })

  it('filters points to the timeframe window, and widening it re-includes them', async () => {
    vi.mocked(fetchDocumentSeries).mockResolvedValue(
      okSeries([
        { date: oldIso(), amount: '80.00', document_id: 1 },
        { date: recentIso(), amount: '100.00', document_id: 2 },
      ]),
    )
    const wrapper = mountTrend(2)
    await flushPromises()

    // Default 12m window keeps only the recent point.
    expect(wrapper.find('[data-testid="tile-stub"]').attributes('data-points')).toBe('1')

    // Switch to "All time" → both points re-enter the window.
    await wrapper.find('[data-testid="charts-timeframe"]').setValue('all')
    await flushPromises()
    expect(wrapper.find('[data-testid="tile-stub"]').attributes('data-points')).toBe('2')
  })

  it('re-groups the forwarded points when the grouping control changes', async () => {
    vi.mocked(fetchDocumentSeries).mockResolvedValue(
      okSeries([{ date: recentIso(), amount: '100.00', document_id: 1 }]),
    )
    const wrapper = mountTrend(1)
    await flushPromises()
    await wrapper.find('[data-testid="charts-grouping"]').setValue('none')
    await flushPromises()
    expect(wrapper.find('[data-testid="tile-stub"]').attributes('data-grouping')).toBe('none')
  })

  it('shows a graceful empty state when the window excludes every point', async () => {
    vi.mocked(fetchDocumentSeries).mockResolvedValue(
      okSeries([{ date: oldIso(), amount: '80.00', document_id: 1 }]),
    )
    const wrapper = mountTrend(1)
    await flushPromises()
    // Default 12m window excludes the 2-year-old point.
    expect(wrapper.find('[data-testid="tile-stub"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="doc-series-empty"]').exists()).toBe(true)
    // Controls stay visible so the user can widen the window.
    expect(wrapper.find('[data-testid="doc-series-controls"]').exists()).toBe(true)
  })

  it('renders nothing when status insufficient', async () => {
    vi.mocked(fetchDocumentSeries).mockResolvedValue({
      status: 'insufficient',
      count: 1,
      document_ids: [3],
    } as never)
    const wrapper = mountTrend(3)
    await flushPromises()
    expect(wrapper.find('[data-testid="tile-stub"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="doc-series-controls"]').exists()).toBe(false)
  })

  it('renders nothing on fetch error (404)', async () => {
    vi.mocked(fetchDocumentSeries).mockRejectedValue(new Error('404'))
    const wrapper = mountTrend(3)
    await flushPromises()
    expect(wrapper.find('[data-testid="tile-stub"]').exists()).toBe(false)
  })
})
