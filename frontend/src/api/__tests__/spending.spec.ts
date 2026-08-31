import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { CellBody, ChartData } from '../spending'
import {
  cellArgs,
  fetchCell,
  fetchChartData,
  fetchFooterBucket,
  listCharts,
  postPreview,
} from '../spending'

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

  // Spec review round 2, finding N5: the footer route declares no `split`
  // param and silently ignores one — sending it would trap a caller into
  // thinking it changed the bucket. `fetchFooterBucket` must drop a stray
  // `split` key BY CONSTRUCTION (picking from/to/currency out by name), not
  // merely because no caller happens to pass one today — cast past the
  // FooterArgs type to simulate a future/misbehaving caller that does.
  it('never forwards a split key to the footer route, even if a caller passed one', async () => {
    respondWith({ bucket: 'uncategorised', total: 0, documents: [] })
    await fetchFooterBucket(1, 'uncategorised', { split: 'category' } as unknown as Parameters<typeof fetchFooterBucket>[2])
    expect(String(fetchMock.mock.calls[0]![0])).not.toContain('split')
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

  // from/to are the aliases; since/until are the field names. Both wire
  // names are asserted absent — a builder that dropped `to` silently (e.g.
  // spelling the query key `until` instead of `to`) would still pass a test
  // that only checked for `since=`.
  it('sends the window as from/to, not since/until', async () => {
    respondWith(DATA)
    await fetchChartData(1, { from: '2026-01-01', to: '2026-08-31' })
    const url = String(fetchMock.mock.calls[0]![0])
    expect(url).toContain('from=2026-01-01')
    expect(url).toContain('to=2026-08-31')
    expect(url).not.toContain('since=')
    expect(url).not.toContain('until=')
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

  // --- preview ---------------------------------------------------------------

  const RULE = { all: [{ facet: 'category', op: 'in' as const, values: ['software'] }] }

  it('posts a rule to the preview route and returns the chart data', async () => {
    respondWith({ ...DATA, chart_id: null, total: '41.00' })
    const data = await postPreview({ rule: RULE, display_currency: 'EUR' })
    const [url, init] = fetchMock.mock.calls[0]!
    expect(String(url)).toContain('/api/spending/preview')
    expect((init as RequestInit).method).toBe('POST')
    expect(JSON.parse(String((init as RequestInit).body))).toMatchObject({ rule: RULE })
    expect(data.total).toBe('41.00')
  })

  // The body-field counterpart of the `split=` query trap above. Here an absent
  // key cannot be misread — there is no saved chart to default from — but the
  // request should still state the axis rather than leave it implied.
  it('sends split explicitly as null rather than omitting the key', async () => {
    respondWith({ ...DATA, chart_id: null })
    await postPreview({ rule: RULE, display_currency: 'EUR', split: null })
    const body = JSON.parse(String((fetchMock.mock.calls[0]![1] as RequestInit).body))
    expect('split' in body).toBe(true)
    expect(body.split).toBeNull()
  })

  it('accepts a preview whose chart_id is null', async () => {
    respondWith({ ...DATA, chart_id: null })
    const data = await postPreview({ rule: RULE, display_currency: 'EUR' })
    expect(data.chart_id).toBeNull()
  })
})
