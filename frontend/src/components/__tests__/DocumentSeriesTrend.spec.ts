import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('vue-chartjs', () => ({ Line: { name: 'Line', template: '<canvas data-testid="chart"/>' } }))
vi.mock('@/api/documents', () => ({ fetchDocumentSeries: vi.fn() }))

import { fetchDocumentSeries } from '@/api/documents'
import DocumentSeriesTrend from '../DocumentSeriesTrend.vue'

describe('DocumentSeriesTrend', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders chart + verdict when status ok', async () => {
    vi.mocked(fetchDocumentSeries).mockResolvedValue({
      status: 'ok', sender: 'Vattenfall', kind: 'utility-bill', currency: 'EUR',
      other_currencies: [], cadence: 'monthly', count: 3, document_ids: [1, 2, 3],
      median: '100.00', reference: { value: '130.00', delta: '30.00', vs_median_pct: '+30.0%', z_score: null, verdict: 'higher' },
      trend: { direction: 'rising', change_pct: '+30.0%' },
      points: [{ date: '2025-01-03', amount: '100.00' }, { date: '2025-03-04', amount: '130.00' }],
    } as never)
    const wrapper = mount(DocumentSeriesTrend, { props: { documentId: 3 } })
    await flushPromises()
    expect(wrapper.find('[data-testid="chart"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('above usual')
  })

  it('renders nothing when status insufficient', async () => {
    vi.mocked(fetchDocumentSeries).mockResolvedValue({
      status: 'insufficient', count: 1, document_ids: [3],
    } as never)
    const wrapper = mount(DocumentSeriesTrend, { props: { documentId: 3 } })
    await flushPromises()
    expect(wrapper.find('[data-testid="series-trend"]').exists()).toBe(false)
  })

  it('renders nothing on fetch error (404)', async () => {
    vi.mocked(fetchDocumentSeries).mockRejectedValue(new Error('404'))
    const wrapper = mount(DocumentSeriesTrend, { props: { documentId: 3 } })
    await flushPromises()
    expect(wrapper.find('[data-testid="series-trend"]').exists()).toBe(false)
  })
})
