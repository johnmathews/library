import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('@/api/documents', () => ({
  fetchCharts: vi.fn(),
  // The view imports seriesId for the per-tile key + deep link; keep the real
  // shape so keys stay stable.
  seriesId: (s: { sender_id: number; kind_id: number; currency: string | null }) =>
    `${s.sender_id}-${s.kind_id}-${s.currency ?? 'none'}`,
}))

import { fetchCharts } from '@/api/documents'
import ChartsView from '../ChartsView.vue'

// Stub the tile (it pulls in chart.js + router); this spec covers the view's
// fetch/render orchestration only.
const TileStub = {
  name: 'SeriesChartTile',
  // Typed so valueless boolean attributes (`editable`, `detail-link`) coerce to
  // true rather than the empty string.
  props: {
    series: { type: Object, required: true },
    highlightDocumentId: Number,
    editable: Boolean,
    detailLink: Boolean,
  },
  template: '<div data-testid="tile-stub">{{ series.sender }}</div>',
}

function mountView() {
  return mount(ChartsView, { global: { stubs: { SeriesChartTile: TileStub } } })
}

function makeSeries(sender: string, senderId: number, kindId: number) {
  return {
    status: 'ok',
    sender,
    kind: 'utility-bill',
    sender_id: senderId,
    kind_id: kindId,
    currency: 'EUR',
    other_currencies: [],
    cadence: 'monthly',
    count: 3,
    document_ids: [1, 2, 3],
    points: [{ date: '2025-01-03', amount: '100.00', document_id: 1 }],
  }
}

describe('ChartsView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders one tile per series returned by the endpoint', async () => {
    vi.mocked(fetchCharts).mockResolvedValue({
      series: [makeSeries('Vattenfall', 1, 2), makeSeries('Eneco', 3, 2)],
    } as never)
    const wrapper = mountView()
    await flushPromises()
    const tiles = wrapper.findAll('[data-testid="tile-stub"]')
    expect(tiles).toHaveLength(2)
    expect(wrapper.find('[data-testid="charts-grid"]').exists()).toBe(true)
    expect(tiles.map((t) => t.text())).toEqual(['Vattenfall', 'Eneco'])
    // Tiles get the detail-link prop so each links to its single-chart page.
    const stubs = wrapper.findAllComponents(TileStub)
    expect(stubs[0]!.props('detailLink')).toBe(true)
    expect(stubs[0]!.props('editable')).toBe(true)
  })

  it('shows an empty state when no series are eligible', async () => {
    vi.mocked(fetchCharts).mockResolvedValue({ series: [] } as never)
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('[data-testid="charts-empty"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="tile-stub"]').exists()).toBe(false)
  })

  it('shows an error state when the fetch fails', async () => {
    vi.mocked(fetchCharts).mockRejectedValue(new Error('500'))
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('[data-testid="charts-error"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="charts-grid"]').exists()).toBe(false)
  })
})
