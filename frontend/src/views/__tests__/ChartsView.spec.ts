import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('@/api/documents', () => ({
  fetchCharts: vi.fn(),
  // The view imports seriesId for the per-tile key + deep link; keep the real
  // shape so keys stay stable.
  seriesId: (s: { sender_id: number; kind_id: number; currency: string | null }) =>
    `${s.sender_id}-${s.kind_id}-${s.currency ?? 'none'}`,
  authoredSeriesId: (id: number) => `a-${id}`,
  createAuthoredSeries: vi.fn(),
  listDocuments: vi.fn(),
}))

import { fetchCharts, createAuthoredSeries, listDocuments } from '@/api/documents'
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
    axisMin: { type: [String, null], default: null },
    axisMax: { type: [String, null], default: null },
    grouping: { type: String, default: 'none' },
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

  it('passes the shared time-axis bounds to each tile when a timeframe is chosen', async () => {
    localStorage.clear()
    vi.mocked(fetchCharts).mockResolvedValue({
      series: [makeSeries('Vattenfall', 1, 2)],
    } as never)
    const wrapper = mountView()
    await flushPromises()

    // Default "last 12 months" → a bounded axis on every tile.
    let stub = wrapper.findComponent(TileStub)
    expect(stub.props('axisMin')).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    expect(stub.props('axisMax')).toMatch(/^\d{4}-\d{2}-\d{2}$/)

    // Choosing "all time" opens the axis (null bounds) on every tile.
    await wrapper.find('[data-testid="charts-timeframe"]').setValue('all')
    stub = wrapper.findComponent(TileStub)
    expect(stub.props('axisMin')).toBeNull()
    expect(stub.props('axisMax')).toBeNull()
  })

  it('drops a tile from the grid when it emits "deleted"', async () => {
    vi.mocked(fetchCharts).mockResolvedValue({
      series: [makeSeries('Vattenfall', 1, 2), makeSeries('Eneco', 3, 2)],
    } as never)
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.findAll('[data-testid="tile-stub"]')).toHaveLength(2)

    // The first tile reports its series was deleted.
    wrapper.findAllComponents(TileStub)[0]!.vm.$emit('deleted')
    await flushPromises()

    const remaining = wrapper.findAll('[data-testid="tile-stub"]')
    expect(remaining).toHaveLength(1)
    expect(remaining[0]!.text()).toBe('Eneco')
    // Removal is local — no refetch needed.
    expect(fetchCharts).toHaveBeenCalledTimes(1)
  })

  it('shows an empty state when no series are eligible', async () => {
    vi.mocked(fetchCharts).mockResolvedValue({ series: [] } as never)
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('[data-testid="charts-empty"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="tile-stub"]').exists()).toBe(false)
  })

  it('applies the chosen grouping to every tile', async () => {
    localStorage.clear()
    vi.mocked(fetchCharts).mockResolvedValue({
      series: [makeSeries('Vattenfall', 1, 2)],
    } as never)
    const wrapper = mountView()
    await flushPromises()
    // Default grouping is "month".
    expect(wrapper.findComponent(TileStub).props('grouping')).toBe('month')

    await wrapper.find('[data-testid="charts-grouping"]').setValue('quarter')
    expect(wrapper.findComponent(TileStub).props('grouping')).toBe('quarter')
  })

  it('shows an error state when the fetch fails', async () => {
    vi.mocked(fetchCharts).mockRejectedValue(new Error('500'))
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('[data-testid="charts-error"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="charts-grid"]').exists()).toBe(false)
  })

  it('creates an authored series via the create flow', async () => {
    vi.mocked(fetchCharts).mockResolvedValue({ series: [] } as never)
    vi.mocked(listDocuments).mockResolvedValue({
      items: [{ id: 42, title: 'Invoice A', currency: 'EUR' }],
    } as never)
    vi.mocked(createAuthoredSeries).mockResolvedValue({ authored_id: 5 } as never)
    const wrapper = mountView()
    await flushPromises()

    // Open the form.
    await wrapper.find('[data-testid="charts-create-button"]').trigger('click')
    expect(wrapper.find('[data-testid="charts-create-form"]').exists()).toBe(true)

    // Fill the name, currency (dropdown) + subtitle.
    await wrapper.find('[data-testid="charts-create-name"]').setValue('My series')
    await wrapper.find('[data-testid="currency-select"]').setValue('EUR')
    await wrapper.find('[data-testid="charts-create-description"]').setValue('Why this matters')

    // Search and add a document.
    await wrapper.find('[data-testid="charts-create-search"]').setValue('inv')
    await wrapper.find('[data-testid="charts-create-search"]').trigger('input')
    await flushPromises()
    await wrapper.find('[data-testid="charts-create-result"]').trigger('click')
    expect(wrapper.find('[data-testid="charts-create-selected"]').text()).toContain('Invoice A')

    // Submit.
    await wrapper.find('[data-testid="charts-create-form"]').trigger('submit')
    await flushPromises()

    expect(createAuthoredSeries).toHaveBeenCalledWith({
      name: 'My series',
      currency: 'EUR',
      description: 'Why this matters',
      document_ids: [42],
    })
    // Form closes + the grid reloads (fetchCharts called again).
    expect(wrapper.find('[data-testid="charts-create-form"]').exists()).toBe(false)
    expect(fetchCharts).toHaveBeenCalledTimes(2)
  })

  it('warns when a selected document currency differs from the chart currency', async () => {
    vi.mocked(fetchCharts).mockResolvedValue({ series: [] } as never)
    vi.mocked(listDocuments).mockResolvedValue({
      items: [{ id: 7, title: 'GBP Invoice', currency: 'GBP' }],
    } as never)
    const wrapper = mountView()
    await flushPromises()

    await wrapper.find('[data-testid="charts-create-button"]').trigger('click')
    await wrapper.find('[data-testid="currency-select"]').setValue('EUR')
    await wrapper.find('[data-testid="charts-create-search"]').setValue('inv')
    await wrapper.find('[data-testid="charts-create-search"]').trigger('input')
    await flushPromises()
    await wrapper.find('[data-testid="charts-create-result"]').trigger('click')

    const warning = wrapper.find('[data-testid="charts-create-currency-warning"]')
    expect(warning.exists()).toBe(true)
    expect(warning.text()).toContain('GBP')
    expect(warning.text()).toContain('EUR')
  })

  it('adds a custom currency code to the dropdown', async () => {
    localStorage.clear()
    vi.mocked(fetchCharts).mockResolvedValue({ series: [] } as never)
    const wrapper = mountView()
    await flushPromises()

    await wrapper.find('[data-testid="charts-create-button"]').trigger('click')
    // Choosing the sentinel reveals the add-a-code input.
    await wrapper.find('[data-testid="currency-select"]').setValue('__add__')
    await wrapper.find('[data-testid="currency-add-input"]').setValue('chf')
    await wrapper.find('[data-testid="currency-add-confirm"]').trigger('click')
    await flushPromises()

    // The new code is now the selected option in the dropdown.
    const select = wrapper.find('[data-testid="currency-select"]')
    expect(select.text()).toContain('CHF')
    expect((select.element as HTMLSelectElement).value).toBe('CHF')
  })

  it('requires a name before creating', async () => {
    vi.mocked(fetchCharts).mockResolvedValue({ series: [] } as never)
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('[data-testid="charts-create-button"]').trigger('click')
    await wrapper.find('[data-testid="charts-create-form"]').trigger('submit')
    await flushPromises()
    expect(createAuthoredSeries).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="charts-create-error"]').exists()).toBe(true)
  })
})
