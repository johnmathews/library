import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('@/api/documents', () => ({ fetchChart: vi.fn() }))

// Drive route.params.seriesId without a real router.
const routeParams = { seriesId: '7-2-EUR' }
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: routeParams }),
  RouterLink: {
    props: ['to'],
    template: '<a :href="to" data-testid="router-link"><slot /></a>',
  },
}))

import { fetchChart } from '@/api/documents'
import SeriesChartView from '../SeriesChartView.vue'

// Stub the tile (pulls in chart.js); this spec covers the view orchestration.
const TileStub = {
  name: 'SeriesChartTile',
  // Typed so the valueless `editable` attribute coerces to true (not '').
  props: { series: { type: Object, required: true }, editable: Boolean },
  template: '<div data-testid="tile-stub">{{ series.sender }}</div>',
}

function mountView() {
  return mount(SeriesChartView, { global: { stubs: { SeriesChartTile: TileStub } } })
}

const series = {
  status: 'ok',
  sender: 'Vattenfall',
  kind: 'utility-bill',
  sender_id: 7,
  kind_id: 2,
  currency: 'EUR',
  other_currencies: [],
  cadence: 'monthly',
  count: 3,
  document_ids: [1, 2, 3],
  points: [{ date: '2025-01-03', amount: '100.00', document_id: 1 }],
}

describe('SeriesChartView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    routeParams.seriesId = '7-2-EUR'
  })

  it('fetches the single series by id and renders one editable tile', async () => {
    vi.mocked(fetchChart).mockResolvedValue(series as never)
    const wrapper = mountView()
    await flushPromises()
    expect(fetchChart).toHaveBeenCalledWith('7-2-EUR')
    const tile = wrapper.find('[data-testid="tile-stub"]')
    expect(tile.exists()).toBe(true)
    expect(tile.text()).toBe('Vattenfall')
    expect(wrapper.findComponent(TileStub).props('editable')).toBe(true)
    // Always offers a way back to the full grid.
    expect(wrapper.find('[data-testid="series-chart-back"]').exists()).toBe(true)
  })

  it('shows a not-found message when the series cannot be loaded', async () => {
    vi.mocked(fetchChart).mockRejectedValue(new Error('404'))
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('[data-testid="series-chart-error"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="tile-stub"]').exists()).toBe(false)
  })
})
