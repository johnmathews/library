import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  addAlias, createFacet, createValue, deleteValue, fetchFacetCounts, fetchLabelCounts,
  mergeValue, renameValue, setValueColour,
} from '../facets'
import { setSenderColour } from '../taxonomy'

function stubFetch(body: unknown = {}, status = 200): ReturnType<typeof vi.fn> {
  const spy = vi.fn().mockResolvedValue({
    ok: status < 400,
    status,
    json: async () => body,
  } as Response)
  vi.stubGlobal('fetch', spy)
  return spy
}

const bodyOf = (spy: ReturnType<typeof vi.fn>): Record<string, unknown> =>
  JSON.parse(spy.mock.calls[0]![1]!.body as string)

afterEach(() => vi.unstubAllGlobals())

describe('the value-edit calls send exactly one field each', () => {
  // The API tells "clear it" from "do not touch it" by whether the key is
  // present (model_fields_set), so a client that spread a form object — which
  // always carries every key — would clear a colour on every rename. Two narrow
  // functions make that unrepresentable; this test is what keeps them narrow.
  it('renameValue sends only label', async () => {
    const spy = stubFetch({ key: 'a', label: 'A', parent_id: null, aliases: [], colour: null })
    await renameValue('category', 'alpha', 'Alpha')
    expect(Object.keys(bodyOf(spy))).toEqual(['label'])
  })

  it('setValueColour sends only colour', async () => {
    const spy = stubFetch({ key: 'a', label: 'A', parent_id: null, aliases: [], colour: '#1283dc' })
    await setValueColour('category', 'alpha', '#1283dc')
    expect(Object.keys(bodyOf(spy))).toEqual(['colour'])
    expect(bodyOf(spy).colour).toBe('#1283dc')
  })

  it('setValueColour(null) sends an explicit null, which survives serialisation', async () => {
    const spy = stubFetch({ key: 'a', label: 'A', parent_id: null, aliases: [], colour: null })
    await setValueColour('category', 'alpha', null)
    expect(spy.mock.calls[0]![1]!.body).toBe('{"colour":null}')
  })

  it('setSenderColour(null) sends an explicit null too', async () => {
    const spy = stubFetch({ id: 1, name: 'X', document_count: 0, colour: null })
    await setSenderColour(1, null)
    expect(spy.mock.calls[0]![1]!.body).toBe('{"colour":null}')
  })
})

describe('routes and methods', () => {
  it('createFacet posts to /api/facets', async () => {
    const spy = stubFetch({ key: 'k' })
    await createFacet('k', 'K', 3)
    expect(spy.mock.calls[0]![0]!).toBe('/api/facets')
    expect(spy.mock.calls[0]![1]!.method).toBe('POST')
    expect(bodyOf(spy)).toEqual({ key: 'k', label: 'K', ordinal: 3 })
  })

  it('createValue posts under the facet', async () => {
    const spy = stubFetch({ key: 'v' })
    await createValue('category', 'v', 'V')
    expect(spy.mock.calls[0]![0]!).toBe('/api/facets/category/values')
  })

  it('addAlias posts to the aliases sub-resource', async () => {
    const spy = stubFetch({ alias: 'x' })
    await addAlias('category', 'alpha', 'x')
    expect(spy.mock.calls[0]![0]!).toBe('/api/facets/category/values/alpha/aliases')
  })

  it('mergeValue carries the target and the dry_run flag', async () => {
    const spy = stubFetch({ moved: 4 })
    const result = await mergeValue('category', 'alpha', 'beta', true)
    expect(spy.mock.calls[0]![0]!).toBe('/api/facets/category/values/alpha/merge')
    expect(bodyOf(spy)).toEqual({ into: 'beta', dry_run: true })
    expect(result.moved).toBe(4)
  })

  it('mergeValue defaults to a real merge only when told to', async () => {
    const spy = stubFetch({ moved: 4 })
    await mergeValue('category', 'alpha', 'beta', false)
    expect(bodyOf(spy).dry_run).toBe(false)
  })

  it('deleteValue issues a DELETE and tolerates the 204 empty body', async () => {
    const spy = vi.fn().mockResolvedValue({ ok: true, status: 204 } as Response)
    vi.stubGlobal('fetch', spy)
    await expect(deleteValue('category', 'alpha')).resolves.toBeUndefined()
    expect(spy.mock.calls[0]![1]!.method).toBe('DELETE')
  })

  it('fetchLabelCounts unwraps the counts array', async () => {
    stubFetch({ counts: [{ facet_key: 'category', value_key: 'alpha', labelled: 7 }] })
    await expect(fetchLabelCounts()).resolves.toEqual([
      { facet_key: 'category', value_key: 'alpha', labelled: 7 },
    ])
  })

  it('no list call this module makes asks for more than 100 rows', async () => {
    // GET /api/documents 422s above limit 100; asserted here because a mocked
    // fetch will never enforce it.
    const spy = stubFetch({ counts: [] })
    await fetchLabelCounts()
    const url = String(spy.mock.calls[0]![0]!)
    const limit = new URL(url, 'http://x').searchParams.get('limit')
    expect(limit === null || Number(limit) <= 100).toBe(true)
  })
})

describe('facet counts (the empty state\'s chart proposals)', () => {
  const COUNT = {
    facet_key: 'category',
    value_key: 'software',
    documents: 12,
    first_date: '2026-01-01',
    last_date: '2026-06-30',
  }

  // A different route from fetchLabelCounts, deliberately: /api/facets/counts
  // reads spend_facts, so it excludes amountless, soft-deleted and
  // non-canonical documents — a value with no money behind it is absent by
  // construction, which is what keeps a moneyless proposal off the empty state.
  it('GETs /api/facets/counts and unwraps the counts envelope', async () => {
    const spy = stubFetch({ counts: [COUNT] })
    const counts = await fetchFacetCounts()
    expect(String(spy.mock.calls[0]![0])).toBe('/api/facets/counts')
    expect(counts).toEqual([COUNT])
  })
})
