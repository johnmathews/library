import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const lineDataCapture = { data: null as unknown, options: null as unknown }
vi.mock('vue-chartjs', () => ({
  Bar: {
    name: 'Bar',
    props: ['data', 'options'],
    template: '<canvas data-testid="chart"/>',
    mounted() {
      lineDataCapture.data = (this as unknown as { data: unknown }).data
      lineDataCapture.options = (this as unknown as { options: unknown }).options
    },
  },
}))

// chartjs-adapter-date-fns registers itself as a side effect; stub it so the
// import resolves under jsdom without pulling the real adapter.
vi.mock('chartjs-adapter-date-fns', () => ({}))

// The tile imports these at runtime for the editable "documents in this series"
// controls (W8). Mock the whole module; the type imports below are erased.
const api = vi.hoisted(() => ({
  addSeriesMember: vi.fn(),
  removeSeriesMember: vi.fn(),
  listDocuments: vi.fn(),
  updateSeriesMeta: vi.fn(),
  // Real shape — the tile uses it for its id + deep link.
  seriesId: (s: { sender_id: number; kind_id: number; currency: string | null }) =>
    `${s.sender_id}-${s.kind_id}-${s.currency ?? 'none'}`,
}))
vi.mock('@/api/documents', () => api)

import SeriesChartTile from '../SeriesChartTile.vue'
import type { DocumentSeries } from '@/api/documents'

const RouterLinkStub = {
  props: ['to'],
  template: '<a :href="to" data-testid="series-citation"><slot /></a>',
}

function mountTile(series: DocumentSeries, highlightDocumentId?: number) {
  return mount(SeriesChartTile, {
    props: { series, highlightDocumentId },
    global: { stubs: { RouterLink: RouterLinkStub } },
  })
}

function mountEditable(series: DocumentSeries = okSeries) {
  return mount(SeriesChartTile, {
    props: { series, editable: true },
    global: { stubs: { RouterLink: RouterLinkStub } },
  })
}

const okSeries: DocumentSeries = {
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
  description: 'Energy bills have risen about 30% since January.',
  median: '100.00',
  reference: {
    value: '130.00',
    delta: '30.00',
    vs_median_pct: '+30.0%',
    z_score: null,
    verdict: 'higher',
  },
  trend: { direction: 'rising', change_pct: '+30.0%' },
  points: [
    { date: '2025-01-03', amount: '100.00', document_id: 1, title: 'January bill' },
    { date: '2025-02-02', amount: '100.00', document_id: 2, title: 'February bill' },
    { date: '2025-03-04', amount: '130.00', document_id: 3, title: null },
  ],
}

describe('SeriesChartTile', () => {
  beforeEach(() => {
    lineDataCapture.data = null
  })

  it('renders the chart, heading and verdict', () => {
    const wrapper = mountTile(okSeries)
    expect(wrapper.find('[data-testid="chart"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="series-heading"]').text()).toContain('Vattenfall')
    expect(wrapper.text()).toContain('above usual')
    expect(wrapper.text()).toContain('trend rising')
  })

  it('renders the cached LLM description when present', () => {
    const wrapper = mountTile(okSeries)
    const desc = wrapper.find('[data-testid="series-description"]')
    expect(desc.exists()).toBe(true)
    expect(desc.text()).toContain('risen about 30%')
  })

  it('omits the description block when absent', () => {
    const wrapper = mountTile({ ...okSeries, description: undefined })
    expect(wrapper.find('[data-testid="series-description"]').exists()).toBe(false)
  })

  it('renders one citation link per point, linking to the document', () => {
    const wrapper = mountTile(okSeries)
    const links = wrapper.findAll('[data-testid="series-citation"]')
    expect(links).toHaveLength(3)
    expect(links.map((l) => l.attributes('href'))).toEqual([
      '/documents/1',
      '/documents/2',
      '/documents/3',
    ])
    // A titled point shows its title; an untitled one falls back to its date.
    expect(links[0]!.text()).toContain('January bill')
    expect(links[2]!.text()).toContain('2025-03-04')
  })

  it('highlights the bar matching highlightDocumentId, not the last bar', () => {
    mountTile(okSeries, 2)
    const captured = lineDataCapture.data as {
      datasets: { backgroundColor: string[] }[]
    }
    const colors = captured.datasets[0]!.backgroundColor
    expect(colors[0]).toBe('#2563eb')
    expect(colors[1]).toBe('#dc2626') // documentId=2 -> middle bar highlighted
    expect(colors[2]).toBe('#2563eb')
  })

  it('highlights the last bar when no highlightDocumentId is given', () => {
    mountTile(okSeries)
    const captured = lineDataCapture.data as {
      datasets: { backgroundColor: string[] }[]
    }
    const colors = captured.datasets[0]!.backgroundColor
    expect(colors[2]).toBe('#dc2626')
  })

  it('plots a temporal x-axis with {x: date, y: amount} points (W9)', () => {
    mountTile(okSeries)
    const options = lineDataCapture.options as { scales: { x: { type: string } } }
    expect(options.scales.x.type).toBe('time')
    const data = lineDataCapture.data as { datasets: { data: { x: string; y: number }[] }[] }
    expect(data.datasets[0]!.data).toEqual([
      { x: '2025-01-03', y: 100 },
      { x: '2025-02-02', y: 100 },
      { x: '2025-03-04', y: 130 },
    ])
  })
})

describe('SeriesChartTile editable membership (W8)', () => {
  beforeEach(() => {
    api.addSeriesMember.mockReset().mockResolvedValue({ state: 'pinned' })
    api.removeSeriesMember.mockReset().mockResolvedValue({ state: 'excluded' })
    api.listDocuments
      .mockReset()
      .mockResolvedValue({ items: [{ id: 42, title: 'Stray bill' }], total: 1, limit: 8, offset: 0 })
  })

  it('shows no edit controls when not editable', () => {
    const wrapper = mountTile(okSeries)
    expect(wrapper.find('[data-testid="series-remove"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="series-add-toggle"]').exists()).toBe(false)
  })

  it('shows no edit controls when editable but the series has no identity', () => {
    const wrapper = mount(SeriesChartTile, {
      props: { series: { ...okSeries, sender_id: null }, editable: true },
      global: { stubs: { RouterLink: RouterLinkStub } },
    })
    expect(wrapper.find('[data-testid="series-add-toggle"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="series-remove"]').exists()).toBe(false)
  })

  it('renders a remove control per document when editable', () => {
    const wrapper = mountEditable()
    expect(wrapper.findAll('[data-testid="series-remove"]')).toHaveLength(3)
  })

  it('removes a document with the series identity + currency, then emits changed', async () => {
    const wrapper = mountEditable()
    await wrapper.find('[data-testid="series-remove"]').trigger('click')
    await flushPromises()
    expect(api.removeSeriesMember).toHaveBeenCalledWith(7, 2, 1, 'EUR')
    expect(wrapper.emitted('changed')).toBeTruthy()
  })

  it('searches then pins a picked document, then emits changed', async () => {
    const wrapper = mountEditable()
    await wrapper.find('[data-testid="series-add-toggle"]').trigger('click')
    await wrapper.find('[data-testid="series-add-search"]').setValue('stray')
    await flushPromises()
    expect(api.listDocuments).toHaveBeenCalledWith({ q: 'stray', limit: 8 })
    const result = wrapper.find('[data-testid="series-add-result"]')
    expect(result.exists()).toBe(true)
    await result.trigger('click')
    await flushPromises()
    expect(api.addSeriesMember).toHaveBeenCalledWith(7, 2, 42, 'EUR')
    expect(wrapper.emitted('changed')).toBeTruthy()
  })

  it('undo after a remove re-adds the document, clearing the override', async () => {
    const wrapper = mountEditable()
    expect(wrapper.find('[data-testid="series-undo"]').exists()).toBe(false)
    await wrapper.find('[data-testid="series-remove"]').trigger('click')
    await flushPromises()
    const undo = wrapper.find('[data-testid="series-undo"]')
    expect(undo.exists()).toBe(true)
    expect(undo.text()).toContain('January bill')
    await wrapper.find('[data-testid="series-undo-button"]').trigger('click')
    await flushPromises()
    // Re-adding the removed doc clears the exclude override.
    expect(api.addSeriesMember).toHaveBeenCalledWith(7, 2, 1, 'EUR')
    expect(wrapper.find('[data-testid="series-undo"]').exists()).toBe(false)
  })
})

describe('SeriesChartTile documents list (W8)', () => {
  it('keeps the documents list collapsed by default and toggles it open', async () => {
    const wrapper = mountTile(okSeries)
    const docs = wrapper.find('[data-testid="series-docs"]')
    // Present in the DOM but hidden (v-show) until toggled.
    expect(docs.attributes('style')).toContain('display: none')
    const toggle = wrapper.find('[data-testid="series-docs-toggle"]')
    expect(toggle.text()).toContain('(3)')
    await toggle.trigger('click')
    expect(wrapper.find('[data-testid="series-docs"]').attributes('style')).not.toContain(
      'display: none',
    )
  })

  it('renders each document as a row with title, date and amount columns', () => {
    const wrapper = mountTile(okSeries)
    const rows = wrapper.findAll('[data-testid="series-citations"] > li')
    expect(rows).toHaveLength(3)
    // First row: title link + its date + its amount, each in its own cell.
    expect(rows[0]!.find('[data-testid="series-citation"]').text()).toContain('January bill')
    expect(rows[0]!.text()).toContain('2025-01-03')
    expect(rows[0]!.text()).toContain('100.00')
  })
})

describe('SeriesChartTile title/description + single-chart link (W12)', () => {
  beforeEach(() => {
    api.updateSeriesMeta.mockReset().mockResolvedValue({ ...okSeries })
  })

  it('uses the derived heading when no title override is set', () => {
    const wrapper = mountTile(okSeries)
    expect(wrapper.find('[data-testid="series-heading"]').text()).toContain(
      'Vattenfall · monthly series',
    )
  })

  it('prefers an override title over the derived heading', () => {
    const wrapper = mountTile({ ...okSeries, title: 'Main flat — energy' })
    const heading = wrapper.find('[data-testid="series-heading"]').text()
    expect(heading).toContain('Main flat — energy')
    expect(heading).not.toContain('monthly series')
  })

  it('shows the deep link only when detailLink is set', () => {
    expect(mountTile(okSeries).find('[data-testid="series-detail-link"]').exists()).toBe(false)
    const wrapper = mount(SeriesChartTile, {
      props: { series: okSeries, detailLink: true },
      global: { stubs: { RouterLink: RouterLinkStub } },
    })
    const link = wrapper.find('[data-testid="series-detail-link"]')
    expect(link.exists()).toBe(true)
    // Stable id: sender-kind-currency.
    expect(link.attributes('href')).toBe('/charts/7-2-EUR')
  })

  it('shows no meta editor unless editable', () => {
    expect(mountTile(okSeries).find('[data-testid="series-meta-edit"]').exists()).toBe(false)
  })

  it('opens an editor prefilled from the current values and saves the override', async () => {
    const wrapper = mountEditable()
    await wrapper.find('[data-testid="series-meta-edit"]').trigger('click')
    const titleInput = wrapper.find('[data-testid="series-title-input"]')
    const descInput = wrapper.find('[data-testid="series-description-input"]')
    expect(titleInput.exists()).toBe(true)
    // Description prefills from the current (cached) description.
    expect((descInput.element as HTMLTextAreaElement).value).toContain('risen about 30%')

    await titleInput.setValue('Main flat — energy')
    await descInput.setValue('Switched tariff in March.')
    await wrapper.find('[data-testid="series-meta-form"]').trigger('submit')
    await flushPromises()

    expect(api.updateSeriesMeta).toHaveBeenCalledWith('7-2-EUR', {
      title: 'Main flat — energy',
      description: 'Switched tariff in March.',
    })
    expect(wrapper.emitted('changed')).toBeTruthy()
  })

  it('clears an override by saving blank inputs (sends null)', async () => {
    const wrapper = mountEditable({ ...okSeries, title: 'Old title' })
    await wrapper.find('[data-testid="series-meta-edit"]').trigger('click')
    await wrapper.find('[data-testid="series-title-input"]').setValue('')
    await wrapper.find('[data-testid="series-description-input"]').setValue('')
    await wrapper.find('[data-testid="series-meta-form"]').trigger('submit')
    await flushPromises()
    expect(api.updateSeriesMeta).toHaveBeenCalledWith('7-2-EUR', {
      title: null,
      description: null,
    })
  })
})
