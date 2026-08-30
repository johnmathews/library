import { afterEach, describe, it, expect, vi } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'

vi.mock('vue-chartjs', () => ({
  Bar: { name: 'Bar', props: ['data', 'options'], template: '<canvas />' },
}))

vi.mock('@/api/spending', async () => {
  const actual = await vi.importActual<typeof import('@/api/spending')>('@/api/spending')
  return { ...actual, updateChart: vi.fn() }
})

import SpendingCard from '../SpendingCard.vue'
import { updateChart, type Cell, type Chart, type ChartData, type Footer, type Grain, type SplitValue } from '@/api/spending'
import { ApiError } from '@/api/client'

const router = createRouter({
  history: createMemoryHistory(),
  routes: [
    { path: '/charts', name: 'charts', component: { template: '<div/>' } },
    { path: '/charts/:chartId', name: 'spending-workspace', component: { template: '<div/>' } },
  ],
})

// --- Fixtures ----------------------------------------------------------
//
// `mountCard()` takes a small convenience shape rather than a full `ChartData`
// — `cells` / `grain` / `footer` / `currency` are spliced into a sensibly
// empty `ChartData` — so a test can say exactly what it needs and nothing
// else. Amounts are invented; this repository is public.

const BASE_CHART: Chart = {
  id: 1,
  name: 'Cloud hosting',
  question_text: 'How much do we spend on cloud hosting?',
  rule: { all: [] },
  default_grain: 'month',
  default_split: null,
  display_currency: 'USD',
  ordinal: 0,
}

function emptyFooter(overrides: Partial<Footer> = {}): Footer {
  return {
    netted_refunds: '0.00',
    refund_count: 0,
    excluded: [],
    unclassified: null,
    uncategorised: null,
    undated: null,
    unaccounted: null,
    unconvertible: [],
    ...overrides,
  }
}

function buildData(overrides: {
  cells?: Cell[]
  grain?: Grain
  footer?: Partial<Footer>
  currency?: string
  splits?: SplitValue[]
} = {}): ChartData {
  return {
    chart_id: 1,
    grain: overrides.grain ?? 'month',
    split: null,
    currency: overrides.currency ?? 'USD',
    since: null,
    until: null,
    cells: overrides.cells ?? [],
    splits: overrides.splits ?? [],
    total: '0.00',
    payments: 0,
    documents: 0,
    footer: emptyFooter(overrides.footer),
  }
}

interface Overrides {
  chart?: Partial<Chart>
  data?: ChartData | null
  error?: string | null
  busy?: boolean
  canMoveUp?: boolean
  canMoveDown?: boolean
  today?: string
  cells?: Cell[]
  grain?: Grain
  footer?: Partial<Footer>
  currency?: string
  splits?: SplitValue[]
}

function mountCard(overrides: Overrides = {}): VueWrapper {
  const data =
    overrides.data !== undefined
      ? overrides.data
      : buildData({
          cells: overrides.cells,
          grain: overrides.grain,
          footer: overrides.footer,
          currency: overrides.currency,
          splits: overrides.splits,
        })
  return mount(SpendingCard, {
    global: { plugins: [router] },
    props: {
      chart: { ...BASE_CHART, ...overrides.chart },
      data,
      error: overrides.error ?? null,
      busy: overrides.busy ?? false,
      canMoveUp: overrides.canMoveUp ?? true,
      canMoveDown: overrides.canMoveDown ?? true,
      today: overrides.today ?? '2026-08-14',
    },
  })
}

// June complete, July complete, August present but PARTIAL (today is
// 2026-08-14, mid-month) — the exact shape a partial-bucket-as-headline bug
// cannot tell apart from a chart that simply has no August cell yet.
const JUNE_JULY_AUGUST: Cell[] = [
  { period: '2026-06-01', split_value: null, total: '900.00', payments: 5 },
  { period: '2026-07-01', split_value: null, total: '1050.00', payments: 6 },
  { period: '2026-08-01', split_value: null, total: '300.00', payments: 2 },
]

// Not floats: `1284.50 - 1142.20` in IEEE754 is `142.29999999999998`.
const TWO_BUCKETS: Overrides = {
  today: '2026-08-14',
  cells: [
    { period: '2026-06-01', split_value: null, total: '1142.20', payments: 4 },
    { period: '2026-07-01', split_value: null, total: '1284.50', payments: 5 },
  ],
}
const RISING: Overrides = TWO_BUCKETS

const WITH_UNCATEGORISED: Overrides = {
  today: '2026-08-14',
  cells: [{ period: '2026-07-01', split_value: null, total: '500.00', payments: 3 }],
  footer: { uncategorised: { amount_kind: 'uncategorised', amount: '45.00', documents: 3 } },
}
const CLEAN: Overrides = {
  today: '2026-08-14',
  cells: [{ period: '2026-07-01', split_value: null, total: '500.00', payments: 3 }],
}
const READY: Overrides = TWO_BUCKETS

// A chart nobody has refreshed in months: no cell anywhere near `today`, not
// even a partial one. A headline that assumes "the last cell is always the
// current partial bucket" (e.g. always takes the second-to-last entry) gets
// this one wrong even though it happens to get JUNE_JULY_AUGUST right.
const DATA_ENDS_BEFORE_TODAY: Overrides = {
  today: '2026-08-14',
  cells: [
    { period: '2026-04-01', split_value: null, total: '400.00', payments: 2 },
    { period: '2026-05-01', split_value: null, total: '420.00', payments: 2 },
    { period: '2026-06-01', split_value: null, total: '410.00', payments: 2 },
  ],
}

// --- Query helpers -------------------------------------------------------

function headline(wrapper: VueWrapper) {
  return wrapper.get('[data-testid="spending-card-headline"]')
}
function delta(wrapper: VueWrapper) {
  return wrapper.get('[data-testid="spending-card-delta"]')
}
function attention(wrapper: VueWrapper) {
  return wrapper.find('[data-testid="spending-card-attention"]')
}
function moveUp(wrapper: VueWrapper) {
  return wrapper.get('[data-testid="spending-card-move-up"]')
}
function moveDown(wrapper: VueWrapper) {
  return wrapper.get('[data-testid="spending-card-move-down"]')
}
function chartLabels(wrapper: VueWrapper): string[] {
  return (wrapper.findComponent({ name: 'Bar' }).props('data') as { labels: string[] }).labels
}
/** Edit / delete / move-up / move-down all live behind the overflow menu —
 * open it before looking for any of them. */
async function openOverflowMenu(wrapper: VueWrapper): Promise<void> {
  await wrapper.get('[data-testid="spending-card-menu"]').trigger('click')
}

describe('SpendingCard', () => {
  afterEach(() => {
    vi.clearAllMocks()
  })


  // --- The brief's pinned assertions --------------------------------------

  it('headlines the most recent COMPLETE bucket, not the current partial one', () => {
    const wrapper = mountCard({ today: '2026-08-14', cells: JUNE_JULY_AUGUST })
    expect(headline(wrapper).text()).toContain('July')
    expect(headline(wrapper).text()).not.toContain('August')
  })

  it('still draws the partial bucket on the chart', () => {
    expect(chartLabels(mountCard({ today: '2026-08-14', cells: JUNE_JULY_AUGUST }))).toContain('2026-08-01')
  })

  it('compares against the bucket before it, exactly', () => {
    expect(delta(mountCard(TWO_BUCKETS)).text()).toContain('142.30')
  })

  it('does not colour the delta as good or bad', () => {
    const el = delta(mountCard(RISING)).element as HTMLElement
    expect(el.className).not.toMatch(/text-(red|green)-/)
  })

  it('renders a needs-attention line only when the footer has one', () => {
    expect(attention(mountCard(WITH_UNCATEGORISED)).text()).toContain('3 documents uncategorised')
    expect(attention(mountCard(CLEAN)).exists()).toBe(false)
  })

  // Each of the four needs-attention buckets gets its own case: a bug that
  // mislabels one (e.g. rendering `unclassified` under the `undated` name)
  // would pass a test that only ever exercises `uncategorised`.
  it.each([
    ['unclassified', { unclassified: { amount_kind: 'unclassified', amount: '12.00', documents: 2 } }, '2 documents unclassified'],
    ['undated', { undated: { amount_kind: 'undated', amount: '18.00', documents: 5 } }, '5 documents undated'],
    ['unaccounted', { unaccounted: { amount_kind: 'unaccounted', amount: '9.00', documents: 1 } }, '1 document unaccounted'],
  ] as const)('names the %s bucket in the needs-attention line', (_bucket, footer, expected) => {
    const wrapper = mountCard({
      today: '2026-08-14',
      cells: [{ period: '2026-07-01', split_value: null, total: '500.00', payments: 3 }],
      footer,
    })
    expect(attention(wrapper).text()).toContain(expected)
  })

  it('renders its own error without hiding the rest of the board', () => {
    expect(mountCard({ data: null, error: 'Could not load this chart.' }).text()).toContain(
      'Could not load this chart.',
    )
  })

  it('keeps edit and delete in the overflow menu, not on the card face', () => {
    const wrapper = mountCard(READY)
    expect(wrapper.find('[data-testid="spending-card-delete"]').exists()).toBe(false)
  })

  it('offers move up and move down as real buttons in the menu, disabled at the ends', async () => {
    const wrapper = mountCard({ canMoveUp: false, canMoveDown: true })
    await openOverflowMenu(wrapper)
    expect(moveUp(wrapper).attributes('disabled')).toBeDefined()
    expect(moveDown(wrapper).attributes('disabled')).toBeUndefined()
  })

  it('keeps the card face free of reorder controls until the menu is opened', () => {
    const wrapper = mountCard({ canMoveUp: true, canMoveDown: true })
    expect(wrapper.find('[data-testid="spending-card-move-up"]').exists()).toBe(false)
  })

  // --- Extra coverage beyond the brief's pinned assertions ----------------

  it('still shows the name and overflow menu when the card errors', () => {
    const wrapper = mountCard({ data: null, error: 'Could not load this chart.', chart: { name: 'Travel' } })
    expect(wrapper.get('[data-testid="spending-card-name"]').text()).toBe('Travel')
    expect(wrapper.find('[data-testid="spending-card-menu"]').exists()).toBe(true)
  })

  it('does not pick the second-to-last bucket when the data ends before today', () => {
    // A chart with no cell anywhere near `today` — June is the true most
    // recent complete bucket. An implementation that assumes "the last
    // period present is always the current partial one" and steps back two
    // would headline May instead.
    const wrapper = mountCard(DATA_ENDS_BEFORE_TODAY)
    expect(headline(wrapper).text()).toContain('June')
    expect(headline(wrapper).text()).not.toContain('May')
  })

  it('computes date_trunc(week, ...) semantics for the current period', () => {
    const wrapper = mountCard({
      today: '2026-08-12', // Wednesday, mid-week
      grain: 'week',
      cells: [
        { period: '2026-07-27', split_value: null, total: '100.00', payments: 1 }, // Monday, complete
        { period: '2026-08-03', split_value: null, total: '120.00', payments: 1 }, // Monday, complete
        { period: '2026-08-10', split_value: null, total: '40.00', payments: 1 }, // Monday, current partial week
      ],
    })
    expect(headline(wrapper).text()).toContain('August 3')
    expect(headline(wrapper).text()).not.toContain('August 10')
  })

  it('computes date_trunc(quarter, ...) semantics for the current period', () => {
    const wrapper = mountCard({
      today: '2026-08-14', // inside Q3 (Jul-Sep)
      grain: 'quarter',
      cells: [
        { period: '2026-01-01', split_value: null, total: '300.00', payments: 1 }, // Q1, complete
        { period: '2026-04-01', split_value: null, total: '350.00', payments: 1 }, // Q2, complete
        { period: '2026-07-01', split_value: null, total: '90.00', payments: 1 }, // Q3, current partial
      ],
    })
    expect(headline(wrapper).text()).toContain('Q2 2026')
    expect(headline(wrapper).text()).not.toContain('Q3 2026')
  })

  it('names a year-grain bucket by its bare year', () => {
    const wrapper = mountCard({
      today: '2027-03-01',
      grain: 'year',
      cells: [
        { period: '2025-01-01', split_value: null, total: '1000.00', payments: 10 },
        { period: '2026-01-01', split_value: null, total: '1200.00', payments: 11 },
        { period: '2027-01-01', split_value: null, total: '80.00', payments: 1 },
      ],
    })
    expect(headline(wrapper).text()).toContain('2026')
    expect(headline(wrapper).text()).not.toContain('2027')
  })

  it('shows a placeholder and no delta when there is no complete bucket yet', () => {
    // Only the current (partial) period has a cell at all.
    const wrapper = mountCard({
      today: '2026-08-14',
      cells: [{ period: '2026-08-01', split_value: null, total: '50.00', payments: 1 }],
    })
    expect(headline(wrapper).text()).toContain('—')
    expect(wrapper.find('[data-testid="spending-card-delta"]').exists()).toBe(false)
  })

  it('shows a headline with no delta when there is only one complete bucket', () => {
    const wrapper = mountCard({
      today: '2026-08-14',
      cells: [{ period: '2026-07-01', split_value: null, total: '500.00', payments: 3 }],
    })
    expect(headline(wrapper).text()).toContain('July')
    expect(wrapper.find('[data-testid="spending-card-delta"]').exists()).toBe(false)
  })

  it('shows a falling delta with a down glyph, still uncoloured', () => {
    const wrapper = mountCard({
      today: '2026-08-14',
      cells: [
        { period: '2026-06-01', split_value: null, total: '1200.00', payments: 4 },
        { period: '2026-07-01', split_value: null, total: '900.00', payments: 4 },
      ],
    })
    const el = delta(wrapper)
    expect(el.text()).toContain('300.00')
    expect((el.element as HTMLElement).className).not.toMatch(/text-(red|green)-/)
  })

  it('dims the body while busy without hiding it', () => {
    const wrapper = mountCard({ ...TWO_BUCKETS, busy: true })
    const body = wrapper.get('[data-testid="spending-card-body"]')
    expect(body.classes()).toContain('opacity-50')
    expect(body.text()).toContain('142.30')
  })

  it('names the card with a link to its workspace — the only route there from the board', () => {
    const wrapper = mountCard({ ...READY, chart: { id: 42, name: 'Cloud hosting' } })
    const link = wrapper.get('[data-testid="spending-card-name"]')
    expect(link.text()).toBe('Cloud hosting')
    expect(link.attributes('href')).toBe('/charts/42')
  })

  // Finding 4: delete has no confirmation — one click, irreversible, sitting
  // directly under the rename item. The overflow item ARMS a Confirm/Cancel
  // pair; only Confirm actually emits.
  describe('delete confirmation', () => {
    it('does not delete the chart until the confirmation is accepted', async () => {
      const wrapper = mountCard(READY)
      await openOverflowMenu(wrapper)
      await wrapper.get('[data-testid="spending-card-delete"]').trigger('click')
      // Armed, not yet deleted.
      expect(wrapper.emitted('delete')).toBeUndefined()
      expect(wrapper.find('[data-testid="spending-card-delete-confirm"]').exists()).toBe(true)

      await wrapper.get('[data-testid="spending-card-delete-confirm"]').trigger('click')
      expect(wrapper.emitted('delete')).toHaveLength(1)
    })

    it('deletes nothing when the confirmation is dismissed', async () => {
      const wrapper = mountCard(READY)
      await openOverflowMenu(wrapper)
      await wrapper.get('[data-testid="spending-card-delete"]').trigger('click')
      await wrapper.get('[data-testid="spending-card-delete-cancel"]').trigger('click')
      expect(wrapper.emitted('delete')).toBeUndefined()
      // Back to the ordinary menu trigger, not stuck in the confirm state.
      expect(wrapper.find('[data-testid="spending-card-menu"]').exists()).toBe(true)
    })
  })

  // Finding 5: "Edit" navigated to the workspace but changed nothing. The
  // menu item is now "Rename" and actually persists a new name.
  describe('rename', () => {
    it('renames the chart on the happy path', async () => {
      const updated: Chart = { ...BASE_CHART, id: 1, name: 'Cloud hosting (renamed)' }
      vi.mocked(updateChart).mockResolvedValueOnce(updated)
      const wrapper = mountCard(READY)
      await openOverflowMenu(wrapper)
      await wrapper.get('[data-testid="spending-card-rename"]').trigger('click')

      const input = wrapper.get('[data-testid="spending-card-rename-input"]')
      expect((input.element as HTMLInputElement).value).toBe('Cloud hosting')
      await input.setValue('Cloud hosting (renamed)')
      await wrapper.get('[data-testid="spending-card-rename-save"]').trigger('click')
      await flushPromises()

      expect(updateChart).toHaveBeenCalledWith(1, { name: 'Cloud hosting (renamed)' })
      expect(wrapper.emitted('renamed')).toEqual([[updated]])
      // Back to the read state — the input is gone, replaced by the link.
      // The DISPLAYED name still reads the (unchanged in this isolated
      // mount) `chart` prop — syncing it is the parent's job in response to
      // `renamed`, covered by SpendingBoardView.spec.ts's own rename test.
      expect(wrapper.find('[data-testid="spending-card-rename-input"]').exists()).toBe(false)
      expect(wrapper.find('[data-testid="spending-card-name"]').exists()).toBe(true)
    })

    it('surfaces an error on a failed rename WITHOUT discarding the typed name', async () => {
      vi.mocked(updateChart).mockRejectedValueOnce(new ApiError(422, 'Name cannot be blank'))
      const wrapper = mountCard(READY)
      await openOverflowMenu(wrapper)
      await wrapper.get('[data-testid="spending-card-rename"]').trigger('click')
      await wrapper.get('[data-testid="spending-card-rename-input"]').setValue('A new name')
      await wrapper.get('[data-testid="spending-card-rename-save"]').trigger('click')
      await flushPromises()

      expect(wrapper.get('[data-testid="spending-card-rename-error"]').text()).toBe('Name cannot be blank')
      expect(wrapper.emitted('renamed')).toBeUndefined()
      // Still editing, and the typed value survives the failure.
      const input = wrapper.get('[data-testid="spending-card-rename-input"]')
      expect((input.element as HTMLInputElement).value).toBe('A new name')
    })

    it('treats a blank or unchanged name as a no-op that just closes the editor', async () => {
      const wrapper = mountCard({ ...READY, chart: { name: 'Cloud hosting' } })
      await openOverflowMenu(wrapper)
      await wrapper.get('[data-testid="spending-card-rename"]').trigger('click')
      await wrapper.get('[data-testid="spending-card-rename-input"]').setValue('Cloud hosting')
      await wrapper.get('[data-testid="spending-card-rename-save"]').trigger('click')
      await flushPromises()

      expect(updateChart).not.toHaveBeenCalled()
      expect(wrapper.find('[data-testid="spending-card-rename-input"]').exists()).toBe(false)
    })

    it('discards the edit on cancel without saving', async () => {
      const wrapper = mountCard(READY)
      await openOverflowMenu(wrapper)
      await wrapper.get('[data-testid="spending-card-rename"]').trigger('click')
      await wrapper.get('[data-testid="spending-card-rename-input"]').setValue('Something else entirely')
      await wrapper.get('[data-testid="spending-card-rename-cancel"]').trigger('click')

      expect(updateChart).not.toHaveBeenCalled()
      expect(wrapper.get('[data-testid="spending-card-name"]').text()).toBe('Cloud hosting')
    })
  })

  it('emits move-up and move-down when clicked, closing the menu after', async () => {
    const wrapper = mountCard(READY)
    await openOverflowMenu(wrapper)
    await moveUp(wrapper).trigger('click')
    expect(wrapper.emitted('move-up')).toHaveLength(1)
    expect(wrapper.find('[data-testid="spending-card-move-up"]').exists()).toBe(false)

    await openOverflowMenu(wrapper)
    await moveDown(wrapper).trigger('click')
    expect(wrapper.emitted('move-down')).toHaveLength(1)
    expect(wrapper.find('[data-testid="spending-card-move-down"]').exists()).toBe(false)
  })

  it('shows no direction glyph when the two buckets are exactly equal', () => {
    const wrapper = mountCard({
      today: '2026-08-14',
      cells: [
        { period: '2026-06-01', split_value: null, total: '500.00', payments: 2 },
        { period: '2026-07-01', split_value: null, total: '500.00', payments: 2 },
      ],
    })
    expect(delta(wrapper).text()).toContain('0.00')
    expect((delta(wrapper).element as HTMLElement).className).not.toMatch(/text-(red|green)-/)
  })

  it('isolates and un-isolates a band on legend click without moving the headline', async () => {
    const splits: SplitValue[] = [
      { value: 'aws', label: 'AWS', colour: null },
      { value: 'gcp', label: 'GCP', colour: null },
    ]
    const wrapper = mountCard({
      today: '2026-08-14',
      splits,
      cells: [
        { period: '2026-07-01', split_value: 'aws', total: '700.00', payments: 3 },
        { period: '2026-07-01', split_value: 'gcp', total: '350.00', payments: 2 },
      ],
    })
    const headlineBefore = headline(wrapper).text()
    const rows = () => wrapper.findAll('[data-testid="spending-legend-row"]')
    expect(rows().length).toBe(2)

    // Isolate AWS: GCP hides, the headline (read straight off `data.cells`,
    // never the display filter) does not move.
    await rows()[0]!.trigger('click')
    expect(rows()[1]!.attributes('aria-pressed')).toBe('false')
    expect(headline(wrapper).text()).toBe(headlineBefore)

    // Clicking the isolated row again clears the isolation.
    await rows()[0]!.trigger('click')
    expect(rows()[1]!.attributes('aria-pressed')).toBe('true')

    // Modifier-click excludes just that one row.
    await rows()[1]!.trigger('click', { ctrlKey: true })
    expect(rows()[1]!.attributes('aria-pressed')).toBe('false')
    expect(rows()[0]!.attributes('aria-pressed')).toBe('true')

    // "Show all" clears every hidden band.
    await wrapper.get('[data-testid="spending-legend-reset"]').trigger('click')
    expect(rows()[1]!.attributes('aria-pressed')).toBe('true')
  })

  it('un-excludes a band on a second modifier-click, without touching Show all', async () => {
    const splits: SplitValue[] = [
      { value: 'aws', label: 'AWS', colour: null },
      { value: 'gcp', label: 'GCP', colour: null },
    ]
    const wrapper = mountCard({
      today: '2026-08-14',
      splits,
      cells: [
        { period: '2026-07-01', split_value: 'aws', total: '700.00', payments: 3 },
        { period: '2026-07-01', split_value: 'gcp', total: '350.00', payments: 2 },
      ],
    })
    const rows = () => wrapper.findAll('[data-testid="spending-legend-row"]')

    // Modifier-click GCP: it hides, AWS stays visible.
    await rows()[1]!.trigger('click', { ctrlKey: true })
    expect(rows()[1]!.attributes('aria-pressed')).toBe('false')
    expect(rows()[0]!.attributes('aria-pressed')).toBe('true')

    // Modifier-click it again: back to where it started, with no "Show all"
    // click involved — `onExclude` toggling off, not `onReset` clearing.
    await rows()[1]!.trigger('click', { ctrlKey: true })
    expect(rows()[1]!.attributes('aria-pressed')).toBe('true')
    expect(rows()[0]!.attributes('aria-pressed')).toBe('true')
    expect(wrapper.find('[data-testid="spending-legend-reset"]').exists()).toBe(false)
  })
})
