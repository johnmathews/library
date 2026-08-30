import { describe, expect, it, vi, beforeEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'

const createChart = vi.fn()
vi.mock('@/api/spending', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/spending')>()),
  createChart: (...args: unknown[]) => createChart(...args),
}))

const fetchFacetCounts = vi.fn()
vi.mock('@/api/facets', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/facets')>()),
  fetchFacetCounts: (...args: unknown[]) => fetchFacetCounts(...args),
}))

import SpendingEmptyState from '../SpendingEmptyState.vue'
import { ApiError } from '@/api/client'
import type { Chart } from '@/api/spending'
import type { FacetCount } from '@/api/facets'

// Values, counts and dates are invented; this repository is public.
const COUNTS: FacetCount[] = [
  { facet_key: 'category', value_key: 'software', documents: 15, first_date: '2026-01-04', last_date: '2026-03-11' },
  { facet_key: 'category', value_key: 'travel', documents: 4, first_date: '2026-02-01', last_date: '2026-02-20' },
]

const SAVED_CHART: Chart = {
  id: 3,
  name: 'All spending',
  question_text: '',
  rule: { all: [] },
  default_grain: 'month',
  default_split: 'category',
  display_currency: 'USD',
  ordinal: 0,
}

async function mountEmpty(counts: FacetCount[], currency = 'USD'): Promise<VueWrapper> {
  fetchFacetCounts.mockResolvedValueOnce(counts)
  const wrapper = mount(SpendingEmptyState, { props: { currency } })
  await flushPromises()
  return wrapper
}

function proposalLabels(wrapper: VueWrapper): string[] {
  return wrapper
    .findAll('[data-testid="spending-empty-proposal-label"]')
    .map((el) => el.text())
}

function proposal(wrapper: VueWrapper, label: string) {
  return wrapper
    .findAll('[data-testid="spending-empty-proposal"]')
    .find((row) => row.text().includes(label))!
}

beforeEach(() => {
  fetchFacetCounts.mockReset()
  createChart.mockReset()
})

describe('SpendingEmptyState', () => {
  // §2.2: the seed is the owner clicking it, through the ordinary save path.
  it('offers All spending first and pinned', async () => {
    const wrapper = await mountEmpty(COUNTS)
    expect(proposalLabels(wrapper)[0]).toBe('All spending')
  })

  it('saves All spending as an empty rule split by category', async () => {
    createChart.mockResolvedValueOnce(SAVED_CHART)
    const wrapper = await mountEmpty(COUNTS)
    await proposal(wrapper, 'All spending').trigger('click')
    await flushPromises()
    expect(createChart).toHaveBeenCalledWith(
      expect.objectContaining({ rule: { all: [] }, default_split: 'category' }),
    )
    expect(wrapper.emitted('created')).toEqual([[SAVED_CHART]])
  })

  // The `category` facet is only seeded via `library label-archive` (an
  // operator step, never automatic on migrate/startup) — a genuinely fresh
  // archive has no facets, and `POST /api/spending` 422s on a split axis the
  // vocabulary doesn't carry. "All spending" is the flagship first-run
  // action, so it must degrade to unsplit rather than fail.
  it('saves All spending unsplit when the archive has no facet vocabulary yet', async () => {
    createChart.mockResolvedValueOnce({ ...SAVED_CHART, default_split: null })
    const wrapper = await mountEmpty([])
    await proposal(wrapper, 'All spending').trigger('click')
    await flushPromises()
    expect(createChart).toHaveBeenCalledWith(
      expect.objectContaining({ rule: { all: [] }, default_split: null }),
    )
  })

  // The description must not promise a split the save will not draw — that
  // is what pins the defect: a description-only fix (or a fix that reverts
  // under mutation) still 422s if the payload itself is not checked, so the
  // save-path assertion above is the one that actually matters and this one
  // guards the second half of the same promise.
  it('labels All spending as one total, not a split, when there is no facet vocabulary yet', async () => {
    const wrapper = await mountEmpty([])
    const row = proposal(wrapper, 'All spending')
    expect(row.text()).toContain('one total')
    expect(row.text()).not.toContain('category')
  })

  it('sends the currency prop on the seed save, never one it chose', async () => {
    createChart.mockResolvedValueOnce(SAVED_CHART)
    const wrapper = await mountEmpty(COUNTS, 'GBP')
    await proposal(wrapper, 'All spending').trigger('click')
    await flushPromises()
    expect(createChart).toHaveBeenCalledWith(expect.objectContaining({ display_currency: 'GBP' }))
  })

  it('proposes facet values with their count and date span', async () => {
    const wrapper = await mountEmpty(COUNTS)
    const row = proposal(wrapper, 'software')
    expect(row.text()).toContain('15 documents')
    expect(row.text()).toContain('2026')
  })

  it('ranks facet proposals by document count, descending', async () => {
    const wrapper = await mountEmpty(COUNTS)
    expect(proposalLabels(wrapper)).toEqual(['All spending', 'software', 'travel'])
  })

  // A value with no money behind it is absent from the response by
  // construction (spend_facts excludes amountless/soft-deleted/non-canonical
  // rows) — this component proposes nothing for it, never fills a gap in.
  it('proposes nothing for a value the counts route did not return', async () => {
    const wrapper = await mountEmpty([])
    expect(proposalLabels(wrapper)).toEqual(['All spending'])
  })

  it('saves a facet proposal as an in-rule over that one value, unsplit', async () => {
    createChart.mockResolvedValueOnce({ ...SAVED_CHART, id: 4, name: 'software' })
    const wrapper = await mountEmpty(COUNTS)
    await proposal(wrapper, 'software').trigger('click')
    await flushPromises()
    expect(createChart).toHaveBeenCalledWith({
      name: 'software',
      rule: { all: [{ facet: 'category', op: 'in', values: ['software'] }] },
      default_split: null,
      display_currency: 'USD',
    })
  })

  it('shows a single date rather than a dash when a value spans one day', async () => {
    const wrapper = await mountEmpty([
      { facet_key: 'category', value_key: 'oneoff', documents: 1, first_date: '2026-05-01', last_date: '2026-05-01' },
    ])
    const row = proposal(wrapper, 'oneoff')
    expect(row.text()).not.toContain('–')
  })

  it('omits the date span when either bound is absent, but still shows the count', async () => {
    const wrapper = await mountEmpty([
      { facet_key: 'category', value_key: 'undated', documents: 2, first_date: null, last_date: null },
    ])
    const row = proposal(wrapper, 'undated')
    expect(row.text()).toContain('2 documents')
  })

  it('shows a load error but still offers All spending', async () => {
    fetchFacetCounts.mockRejectedValueOnce(new ApiError(500, 'boom'))
    const wrapper = mount(SpendingEmptyState, { props: { currency: 'USD' } })
    await flushPromises()
    expect(wrapper.get('[data-testid="spending-empty-load-error"]').text()).toBe('boom')
    expect(proposalLabels(wrapper)).toEqual(['All spending'])
  })

  it('falls back to a generic load error for a non-API failure', async () => {
    fetchFacetCounts.mockRejectedValueOnce(new Error('network down'))
    const wrapper = mount(SpendingEmptyState, { props: { currency: 'USD' } })
    await flushPromises()
    expect(wrapper.get('[data-testid="spending-empty-load-error"]').text()).toBe(
      'Could not load proposed questions.',
    )
  })

  it('shows a save error from the API and does not emit created', async () => {
    createChart.mockRejectedValueOnce(new ApiError(409, 'A chart with this name already exists.'))
    const wrapper = await mountEmpty(COUNTS)
    await proposal(wrapper, 'All spending').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="spending-empty-save-error"]').text()).toBe(
      'A chart with this name already exists.',
    )
    expect(wrapper.emitted('created')).toBeUndefined()
  })

  it('falls back to a generic save error for a non-API failure', async () => {
    createChart.mockRejectedValueOnce(new Error('network down'))
    const wrapper = await mountEmpty(COUNTS)
    await proposal(wrapper, 'All spending').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="spending-empty-save-error"]').text()).toBe('Could not create this chart.')
  })

  it('disables a proposal while its own save is in flight, without disabling the others', async () => {
    let resolveCreate!: (value: Chart) => void
    createChart.mockReturnValueOnce(new Promise<Chart>((resolve) => { resolveCreate = resolve }))
    const wrapper = await mountEmpty(COUNTS)
    const allSpendingRow = proposal(wrapper, 'All spending')
    void allSpendingRow.trigger('click')
    await flushPromises()
    expect(allSpendingRow.attributes('disabled')).toBeDefined()
    expect(proposal(wrapper, 'software').attributes('disabled')).toBeUndefined()
    resolveCreate(SAVED_CHART)
    await flushPromises()
  })

  it('caps the counts request to one call on mount', async () => {
    await mountEmpty(COUNTS)
    expect(fetchFacetCounts).toHaveBeenCalledTimes(1)
  })
})
