import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const lineDataCapture = { data: null as unknown }
vi.mock('vue-chartjs', () => ({
  Line: {
    name: 'Line',
    props: ['data', 'options'],
    template: '<canvas data-testid="chart"/>',
    mounted() { lineDataCapture.data = (this as unknown as { data: unknown }).data },
  },
}))
vi.mock('@/api/documents', () => ({ fetchDocumentSeries: vi.fn() }))

import { fetchDocumentSeries } from '@/api/documents'
import DocumentSeriesTrend from '../DocumentSeriesTrend.vue'

describe('DocumentSeriesTrend', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    lineDataCapture.data = null
  })

  it('renders chart + verdict when status ok', async () => {
    vi.mocked(fetchDocumentSeries).mockResolvedValue({
      status: 'ok', sender: 'Vattenfall', kind: 'utility-bill', currency: 'EUR',
      other_currencies: [], cadence: 'monthly', count: 3, document_ids: [1, 2, 3],
      median: '100.00', reference: { value: '130.00', delta: '30.00', vs_median_pct: '+30.0%', z_score: null, verdict: 'higher' },
      trend: { direction: 'rising', change_pct: '+30.0%' },
      points: [
        { date: '2025-01-03', amount: '100.00', document_id: 1 },
        { date: '2025-03-04', amount: '130.00', document_id: 3 },
      ],
    } as never)
    const wrapper = mount(DocumentSeriesTrend, { props: { documentId: 3 } })
    await flushPromises()
    expect(wrapper.find('[data-testid="chart"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('above usual')
  })

  it('highlights the point matching documentId, not the last point', async () => {
    vi.mocked(fetchDocumentSeries).mockResolvedValue({
      status: 'ok', sender: 'Acme', kind: 'invoice', currency: 'EUR',
      other_currencies: [], cadence: 'monthly', count: 3, document_ids: [1, 2, 3],
      median: '100.00',
      points: [
        { date: '2025-01-01', amount: '90.00', document_id: 1 },
        { date: '2025-02-01', amount: '100.00', document_id: 2 },
        { date: '2025-03-01', amount: '110.00', document_id: 3 },
      ],
    } as never)
    // documentId=2 is the MIDDLE point (index 1), not the last (index 2)
    mount(DocumentSeriesTrend, { props: { documentId: 2 } })
    await flushPromises()
    const captured = lineDataCapture.data as { datasets: { pointBackgroundColor: string[] }[] }
    const colors = captured.datasets[0]!.pointBackgroundColor
    expect(colors[0]).toBe('#2563eb') // index 0 — normal
    expect(colors[1]).toBe('#dc2626') // index 1 — highlighted (documentId=2)
    expect(colors[2]).toBe('#2563eb') // index 2 — normal
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
