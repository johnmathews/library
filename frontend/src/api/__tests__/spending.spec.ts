import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { CellBody, ChartData } from '../spending'
import { cellArgs, fetchCell, fetchChartData, fetchFooterBucket, listCharts } from '../spending'

describe('spending API', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
  })

  afterEach(() => vi.unstubAllGlobals())

  function respondWith(body: unknown, status = 200): void {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
  }

  const DATA: ChartData = {
    chart_id: 1,
    grain: 'month',
    split: null,
    currency: 'GBP',
    since: null,
    until: null,
    cells: [],
    splits: [],
    total: '0.00',
    payments: 0,
    documents: 0,
    footer: {
      netted_refunds: '0.00',
      refund_count: 0,
      excluded: [],
      unclassified: null,
      uncategorised: null,
      undated: null,
      unaccounted: null,
      unconvertible: [],
    },
  }

  const CELL: CellBody = {
    period: '2026-01-01',
    split_value: 'software',
    total: '0.00',
    payments: [],
    label: 'Software',
    colour: null,
  }

  it('caps limit at the server maximum', async () => {
    respondWith({ charts: [] })
    await listCharts(500)
    expect(String(fetchMock.mock.calls[0]![0])).toContain('limit=100')
  })

  it('caps the footer bucket limit too', async () => {
    respondWith({ bucket: 'uncategorised', total: 0, documents: [] })
    await fetchFooterBucket(1, 'uncategorised', { limit: 500 })
    expect(String(fetchMock.mock.calls[0]![0])).toContain('limit=100')
  })

  // The split trap, both directions.
  it('sends split= when the split is cleared', async () => {
    respondWith(DATA)
    await fetchChartData(1, { split: null })
    expect(String(fetchMock.mock.calls[0]![0])).toMatch(/[?&]split=(&|$)/)
  })

  it('omits split entirely when the caller did not supply it', async () => {
    respondWith(DATA)
    await fetchChartData(1, { grain: 'month' })
    expect(String(fetchMock.mock.calls[0]![0])).not.toContain('split=')
  })

  // from/to are the aliases; since/until are the field names.
  it('sends the window as from/to, not since/until', async () => {
    respondWith(DATA)
    await fetchChartData(1, { from: '2026-01-01', to: '2026-08-31' })
    const url = String(fetchMock.mock.calls[0]![0])
    expect(url).toContain('from=2026-01-01')
    expect(url).toContain('to=2026-08-31')
    expect(url).not.toContain('since=')
  })

  it('echoes /data resolved arguments into /cell verbatim', async () => {
    const data = {
      ...DATA,
      grain: 'quarter',
      split: 'category',
      currency: 'GBP',
      since: '2026-01-01',
      until: '2026-06-30',
    }
    respondWith(CELL)
    await fetchCell(1, '2026-01-01', 'software', cellArgs(data as ChartData))
    const url = String(fetchMock.mock.calls[0]![0])
    for (const part of [
      'grain=quarter',
      'split=category',
      'currency=GBP',
      'from=2026-01-01',
      'to=2026-06-30',
      'period=2026-01-01',
      'split_value=software',
    ]) {
      expect(url).toContain(part)
    }
  })

  it('omits split_value for the unlabelled bucket', async () => {
    respondWith(CELL)
    await fetchCell(1, '2026-01-01', null, {})
    expect(String(fetchMock.mock.calls[0]![0])).not.toContain('split_value=')
  })
})
