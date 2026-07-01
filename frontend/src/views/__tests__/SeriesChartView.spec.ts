import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('@/api/documents', () => ({ fetchChart: vi.fn() }))

// Drive route.params.seriesId without a real router; capture navigations.
const routeParams = { seriesId: '7-2-EUR' }
const push = vi.fn()
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: routeParams }),
  useRouter: () => ({ push }),
  RouterLink: {
    props: ['to'],
    template: '<a :href="to" data-testid="router-link"><slot /></a>',
  },
}))

// Mock the export util so we can assert wiring without touching a real canvas.
vi.mock('@/utils/chartExport', () => ({
  downloadImage: vi.fn(),
  downloadPdf: vi.fn(),
  copyShareUrl: vi.fn().mockResolvedValue(undefined),
}))

import { fetchChart } from '@/api/documents'
import { downloadImage, downloadPdf, copyShareUrl } from '@/utils/chartExport'
import SeriesChartView from '../SeriesChartView.vue'

// A stand-in canvas the tile hands back for export.
const stubCanvas = { toDataURL: () => 'data:image/png;base64,AAAA' } as unknown as HTMLCanvasElement

// Stub the tile (pulls in chart.js); this spec covers the view orchestration.
const TileStub = {
  name: 'SeriesChartTile',
  // Typed so the valueless `editable` attribute coerces to true (not '').
  props: {
    series: { type: Object, required: true },
    editable: Boolean,
    size: { type: String, default: 'default' },
    grouping: { type: String, default: 'none' },
    axisMin: { type: [String, null], default: null },
    axisMax: { type: [String, null], default: null },
  },
  // Expose the same handle the real tile does so export wiring can be tested.
  methods: {
    getChartCanvas() {
      return stubCanvas
    },
  },
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
    // Full-screen view: the tile renders large, with the shared controls above it.
    expect(wrapper.findComponent(TileStub).props('size')).toBe('large')
    expect(wrapper.find('[data-testid="chart-controls"]').exists()).toBe(true)
    // No longer boxed into a narrow column.
    expect(wrapper.html()).not.toContain('max-w-2xl')
    // Always offers a way back to the full grid.
    expect(wrapper.find('[data-testid="series-chart-back"]').exists()).toBe(true)
  })

  it('exports the chart as PDF / JPEG and copies the share link', async () => {
    vi.mocked(fetchChart).mockResolvedValue(series as never)
    const wrapper = mountView()
    await flushPromises()

    await wrapper.find('[data-testid="chart-export-pdf"]').trigger('click')
    expect(downloadPdf).toHaveBeenCalledWith(stubCanvas, 'Vattenfall · monthly series', 'Vattenfall · monthly series')

    await wrapper.find('[data-testid="chart-export-jpeg"]').trigger('click')
    expect(downloadImage).toHaveBeenCalledWith(stubCanvas, 'jpeg', 'Vattenfall · monthly series')

    await wrapper.find('[data-testid="chart-share"]').trigger('click')
    await flushPromises()
    expect(copyShareUrl).toHaveBeenCalled()
    expect(wrapper.find('[data-testid="chart-share"]').text()).toContain('Link copied')
  })

  it('returns to the grid when the tile reports the series was deleted', async () => {
    vi.mocked(fetchChart).mockResolvedValue(series as never)
    const wrapper = mountView()
    await flushPromises()
    wrapper.findComponent(TileStub).vm.$emit('deleted')
    await flushPromises()
    expect(push).toHaveBeenCalledWith('/charts')
  })

  it('shows a not-found message when the series cannot be loaded', async () => {
    vi.mocked(fetchChart).mockRejectedValue(new Error('404'))
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('[data-testid="series-chart-error"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="tile-stub"]').exists()).toBe(false)
  })
})
