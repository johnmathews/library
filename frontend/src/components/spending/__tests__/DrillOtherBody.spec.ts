import { describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import DrillOtherBody from '../DrillOtherBody.vue'
import type { Cell, SplitValue } from '@/api/spending'

// This body must issue no request at all (the whole point of the fold
// costing nothing extra) — spy on fetchCell to prove it, even though the
// component doesn't import `@/api/spending` at all today; the guard still
// catches a later change that adds one.
const fetchCell = vi.fn()
vi.mock('@/api/spending', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/spending')>()),
  fetchCell: (...args: unknown[]) => fetchCell(...args),
}))

const TAIL: SplitValue[] = [
  { value: 'insurance', label: 'Insurance', colour: null },
  { value: 'training', label: 'Training', colour: null },
  { value: 'postage', label: 'Postage', colour: null },
]

const CELLS: Cell[] = [
  { period: '2026-08-01', split_value: 'insurance', total: '180.00', payments: 2 },
  { period: '2026-08-01', split_value: 'training', total: '121.10', payments: 1 },
  { period: '2026-08-01', split_value: 'postage', total: '64.50', payments: 3 },
]

// One folded value with cells across three periods — August is the only one
// that should ever be rendered when `period` is '2026-08-01'.
const CELLS_ACROSS_MONTHS: Cell[] = [
  { period: '2026-07-01', split_value: 'insurance', total: '999.00', payments: 5 },
  { period: '2026-08-01', split_value: 'insurance', total: '180.00', payments: 2 },
  { period: '2026-09-01', split_value: 'insurance', total: '50.00', payments: 1 },
]

function mountOtherBody(
  overrides: Partial<{ period: string; members: SplitValue[]; cells: Cell[]; currency: string }> = {},
): VueWrapper {
  return mount(DrillOtherBody, {
    props: {
      period: '2026-08-01',
      members: TAIL,
      cells: CELLS,
      currency: 'EUR',
      ...overrides,
    },
  })
}

function rowLabels(wrapper: VueWrapper): string[] {
  return wrapper.findAll('[data-testid="drill-other-label"]').map((row) => row.text())
}

function rowAmounts(wrapper: VueWrapper): string[] {
  return wrapper.findAll('[data-testid="drill-other-amount"]').map((row) => row.text())
}

function rowOf(wrapper: VueWrapper, label: string) {
  const row = wrapper
    .findAll('[data-testid="drill-other-row"]')
    .find((candidate) => candidate.text().includes(label))
  if (!row) throw new Error(`no row for ${label}`)
  return row
}

describe('DrillOtherBody', () => {
  // The folded values' totals for this period are already in /data's cells,
  // so this step costs no request.
  it('lists the folded values for the period without fetching anything', () => {
    const wrapper = mountOtherBody({ period: '2026-08-01', members: TAIL, cells: CELLS })
    expect(rowLabels(wrapper)).toEqual(['Insurance', 'Training', 'Postage'])
    expect(rowAmounts(wrapper)).toEqual(['EUR 180.00', 'EUR 121.10', 'EUR 64.50'])
    expect(fetchCell).not.toHaveBeenCalled()
  })

  it('emits the raw value when a row is picked, so /cell can round-trip it', async () => {
    const wrapper = mountOtherBody()
    await rowOf(wrapper, 'Training').trigger('click')
    expect(wrapper.emitted('pick')![0]).toEqual(['training'])
  })

  // The bar that was clicked is one period; the fold's members have cells in
  // others too, and listing those would not add up to the segment.
  it('shows only this period, not the whole window', () => {
    const wrapper = mountOtherBody({
      period: '2026-08-01',
      members: [{ value: 'insurance', label: 'Insurance', colour: null }],
      cells: CELLS_ACROSS_MONTHS,
    })
    expect(rowAmounts(wrapper)).toEqual(['EUR 180.00'])
  })

  it('renders a zero row for a member with no cell in this period, rather than dropping it', () => {
    const wrapper = mountOtherBody({
      period: '2026-08-01',
      members: [...TAIL, { value: 'training_extra', label: 'Extra training', colour: null }],
      cells: CELLS,
    })
    expect(rowLabels(wrapper)).toEqual(['Insurance', 'Training', 'Postage', 'Extra training'])
    expect(rowAmounts(wrapper).at(-1)).toBe('EUR 0.00')
  })

  it('renders the unlabelled split value (null) as its own row', () => {
    const wrapper = mountOtherBody({
      period: '2026-08-01',
      members: [{ value: null, label: 'Unlabelled', colour: null }],
      cells: [{ period: '2026-08-01', split_value: null, total: '12.00', payments: 1 }],
    })
    expect(rowLabels(wrapper)).toEqual(['Unlabelled'])
    expect(rowAmounts(wrapper)).toEqual(['EUR 12.00'])
  })

  it('shows an empty state when there are no folded members', () => {
    const wrapper = mountOtherBody({ members: [] })
    expect(wrapper.find('[data-testid="drill-empty"]').exists()).toBe(true)
  })
})
