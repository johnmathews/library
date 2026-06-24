import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('@/api/documents', () => ({ fetchDocumentSeries: vi.fn() }))

import { fetchDocumentSeries } from '@/api/documents'
import DocumentSeriesTrend from '../DocumentSeriesTrend.vue'

// Stub the presentational tile: this spec only covers the fetch/delegate
// behaviour of the wrapper (the tile has its own spec).
const TileStub = {
  name: 'SeriesChartTile',
  props: ['series', 'highlightDocumentId'],
  template:
    '<div data-testid="tile-stub">{{ series.sender }}|{{ highlightDocumentId }}</div>',
}

function mountTrend(documentId: number) {
  return mount(DocumentSeriesTrend, {
    props: { documentId },
    global: { stubs: { SeriesChartTile: TileStub } },
  })
}

describe('DocumentSeriesTrend', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders the tile and passes the series + highlight id when status ok', async () => {
    vi.mocked(fetchDocumentSeries).mockResolvedValue({
      status: 'ok',
      sender: 'Vattenfall',
      kind: 'utility-bill',
      currency: 'EUR',
      other_currencies: [],
      cadence: 'monthly',
      count: 3,
      document_ids: [1, 2, 3],
      points: [{ date: '2025-01-03', amount: '100.00', document_id: 1 }],
    } as never)
    const wrapper = mountTrend(3)
    await flushPromises()
    const tile = wrapper.find('[data-testid="tile-stub"]')
    expect(tile.exists()).toBe(true)
    expect(tile.text()).toBe('Vattenfall|3') // highlightDocumentId == documentId
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
  })

  it('renders nothing on fetch error (404)', async () => {
    vi.mocked(fetchDocumentSeries).mockRejectedValue(new Error('404'))
    const wrapper = mountTrend(3)
    await flushPromises()
    expect(wrapper.find('[data-testid="tile-stub"]').exists()).toBe(false)
  })
})
