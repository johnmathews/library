import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount } from '@vue/test-utils'

// Capture router navigations.
const push = vi.fn()
vi.mock('vue-router', () => ({
  useRouter: () => ({ push }),
  RouterLink: {
    props: ['to'],
    template: '<a :href="to"><slot /></a>',
  },
}))

// The tile pulls in chart.js; stub the renderer + its date adapter.
vi.mock('vue-chartjs', () => ({
  Bar: { name: 'Bar', props: ['data', 'options'], template: '<canvas />' },
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
