import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount } from '@vue/test-utils'

const lineDataCapture = { data: null as unknown }
vi.mock('vue-chartjs', () => ({
  Line: {
    name: 'Line',
    props: ['data', 'options'],
    template: '<canvas data-testid="chart"/>',
    mounted() {
      lineDataCapture.data = (this as unknown as { data: unknown }).data
    },
  },
}))

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

  it('highlights the point matching highlightDocumentId, not the last point', () => {
    mountTile(okSeries, 2)
    const captured = lineDataCapture.data as {
      datasets: { pointBackgroundColor: string[] }[]
    }
    const colors = captured.datasets[0]!.pointBackgroundColor
    expect(colors[0]).toBe('#2563eb')
    expect(colors[1]).toBe('#dc2626') // documentId=2 -> middle point highlighted
    expect(colors[2]).toBe('#2563eb')
  })

  it('highlights the last point when no highlightDocumentId is given', () => {
    mountTile(okSeries)
    const captured = lineDataCapture.data as {
      datasets: { pointBackgroundColor: string[] }[]
    }
    const colors = captured.datasets[0]!.pointBackgroundColor
    expect(colors[2]).toBe('#dc2626')
  })
})
