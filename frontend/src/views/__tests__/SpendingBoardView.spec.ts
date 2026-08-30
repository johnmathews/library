import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'

vi.mock('vue-chartjs', () => ({
  Bar: { name: 'Bar', props: ['data', 'options'], template: '<canvas />' },
}))

// SortableJS relies on native HTML5 drag events jsdom does not implement, so
// real drag gestures are out of reach here — the brief is explicit that drag
// is asserted on chromium only, in e2e. What a unit test CAN prove is the
// load-bearing claim that drag and the keyboard path share one function: this
// mock captures the `onEnd` callback the board hands to `Sortable.create` so
// a test can invoke it directly, exactly as SortableJS would after a real
// drop, and it also stands in for the destroy() lifecycle so cleanup on
// unmount/hide is provable without a live drag session.
let capturedSortableOptions:
  | { onEnd: (evt: { oldIndex?: number | null; newIndex?: number | null }) => void; filter?: string }
  | null = null
const sortableDestroy = vi.fn()
vi.mock('sortablejs', () => ({
  default: {
    create: vi.fn((_el: unknown, options: typeof capturedSortableOptions) => {
      capturedSortableOptions = options
      return { destroy: sortableDestroy }
    }),
  },
}))

vi.mock('@/api/spending', async () => {
  const actual = await vi.importActual<typeof import('@/api/spending')>('@/api/spending')
  return {
    ...actual,
    listCharts: vi.fn(),
    fetchChartData: vi.fn(),
    updateChart: vi.fn(),
    deleteChart: vi.fn(),
    createChart: vi.fn(),
    draftQuestion: vi.fn(),
  }
})
vi.mock('@/api/facets', () => ({
  fetchFacetCounts: vi.fn(),
}))

import {
  createChart,
  deleteChart,
  fetchChartData,
  listCharts,
  updateChart,
  type Chart,
  type ChartData,
} from '@/api/spending'
import { ApiError } from '@/api/client'
import { fetchFacetCounts } from '@/api/facets'
import SpendingBoardView from '../SpendingBoardView.vue'
import QuestionDraft from '@/components/spending/QuestionDraft.vue'
import SpendingEmptyState from '@/components/spending/SpendingEmptyState.vue'

// --- Fixtures ----------------------------------------------------------
//
// This repository is public — every chart name and amount below is invented.

function chart(overrides: Partial<Chart> & { id: number }): Chart {
  return {
    name: `Chart ${overrides.id}`,
    question_text: 'How much do we spend?',
    rule: { all: [] },
    default_grain: 'month',
    default_split: null,
    display_currency: 'EUR',
    ordinal: overrides.id,
    ...overrides,
  }
}

const THREE_CHARTS: Chart[] = [
  chart({ id: 1, name: 'Chart 1', ordinal: 0 }),
  chart({ id: 2, name: 'Chart 2', ordinal: 1 }),
  chart({ id: 3, name: 'Chart 3', ordinal: 2 }),
]

function emptyData(chartId: number): ChartData {
  return {
    chart_id: chartId,
    grain: 'month',
    split: null,
    currency: 'EUR',
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
}

const router = createRouter({
  history: createMemoryHistory(),
  routes: [
    { path: '/charts', name: 'charts', component: SpendingBoardView },
    { path: '/charts/:chartId', name: 'spending-workspace', component: { template: '<div/>' } },
  ],
})

async function mountedBoard(charts: Chart[]): Promise<VueWrapper> {
  vi.mocked(listCharts).mockResolvedValue(charts)
  await router.push('/charts')
  await router.isReady()
  const wrapper = mount(SpendingBoardView, { global: { plugins: [router] } })
  await flushPromises()
  return wrapper
}

function cards(wrapper: VueWrapper) {
  return wrapper.findAll('[data-testid="spending-card"]')
}
function cardAt(wrapper: VueWrapper, index: number) {
  const found = cards(wrapper)[index]
  if (!found) throw new Error(`no card at index ${index}`)
  return found
}
async function moveDown(card: ReturnType<typeof cardAt>) {
  await card.get('[data-testid="spending-card-menu"]').trigger('click')
  return card.get('[data-testid="spending-card-move-down"]')
}
async function moveUp(card: ReturnType<typeof cardAt>) {
  await card.get('[data-testid="spending-card-menu"]').trigger('click')
  return card.get('[data-testid="spending-card-move-up"]')
}

describe('SpendingBoardView', () => {
  beforeEach(() => {
    // The board persists its currency choice in localStorage
    // (`library:charts-board-currency`) — start every test from "nothing
    // stored" so the default-option assertions don't depend on test order.
    localStorage.clear()
  })
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('orders cards by ordinal then name, never by document count', async () => {
    // Two charts tie on ordinal 1; the tie-break is the NAME, alphabetically —
    // "Beta" before "Zeta" — never a document count (which lives on a card's
    // fetched data, not on `Chart`, and this proves it is never consulted).
    const unordered: Chart[] = [
      chart({ id: 1, name: 'Zeta spending', ordinal: 1 }),
      chart({ id: 2, name: 'Alpha spending', ordinal: 0 }),
      chart({ id: 3, name: 'Beta spending', ordinal: 1 }),
    ]
    vi.mocked(fetchChartData).mockImplementation((id) => Promise.resolve(emptyData(id)))
    const wrapper = await mountedBoard(unordered)
    const names = wrapper
      .findAll('[data-testid="spending-card-name"]')
      .map((el) => el.text())
    expect(names).toEqual(['Alpha spending', 'Beta spending', 'Zeta spending'])
  })

  it('loads every chart in parallel and renders a failed one inline', async () => {
    vi.mocked(fetchChartData).mockImplementation((id) =>
      id === 2 ? Promise.reject(new ApiError(500, 'boom')) : Promise.resolve(emptyData(id)),
    )
    const wrapper = await mountedBoard(THREE_CHARTS)
    expect(cards(wrapper)).toHaveLength(3)
    expect(cardAt(wrapper, 1).text()).toContain('boom')
    expect(wrapper.find('[data-testid="board-banner"]').exists()).toBe(false)
    // The two charts that DID load still render their body, proving the
    // failure did not hide them.
    expect(cardAt(wrapper, 0).find('[data-testid="spending-card-body"]').exists()).toBe(true)
    expect(cardAt(wrapper, 2).find('[data-testid="spending-card-body"]').exists()).toBe(true)
  })

  it('moves a card down and persists only the ordinals that changed', async () => {
    // Charts 1, 2, 3 at ordinals 0, 1, 2. Moving the FIRST card down swaps it
    // with the second, so chart 2 takes ordinal 0 and chart 1 takes ordinal 1.
    // Chart 3 does not move and must not be PATCHed. The two calls have no
    // required order, so sort before comparing.
    vi.mocked(fetchChartData).mockImplementation((id) => Promise.resolve(emptyData(id)))
    vi.mocked(updateChart).mockImplementation((id, patch) =>
      Promise.resolve({ ...THREE_CHARTS.find((c) => c.id === id)!, ...patch }),
    )
    const wrapper = await mountedBoard(THREE_CHARTS)
    const downButton = await moveDown(cardAt(wrapper, 0))
    await downButton.trigger('click')
    await flushPromises()
    const calls = vi
      .mocked(updateChart)
      .mock.calls.map((c) => [c[0], c[1].ordinal])
      .sort((a, b) => Number(a[0]) - Number(b[0]))
    expect(calls).toEqual([
      [1, 1],
      [2, 0],
    ])
  })

  it('shows the empty state when there are no charts, and the board after saving one', async () => {
    vi.mocked(fetchFacetCounts).mockResolvedValue([])
    const created = chart({ id: 9, name: 'All spending', ordinal: 0, default_split: 'category' })
    vi.mocked(createChart).mockResolvedValue(created)
    vi.mocked(fetchChartData).mockResolvedValue(emptyData(9))

    const wrapper = await mountedBoard([])
    expect(wrapper.find('[data-testid="spending-empty-state"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="spending-card"]').exists()).toBe(false)

    await wrapper.get('[data-testid="spending-empty-proposal"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="spending-empty-state"]').exists()).toBe(false)
    expect(cards(wrapper)).toHaveLength(1)
    expect(wrapper.get('[data-testid="spending-card-name"]').text()).toBe('All spending')
  })

  // Carried from Task 7's review: a card with `busy` true and no data yet
  // renders no body at all, so a first load would show a board of empty
  // rectangles. The board owns the load lifecycle, so the placeholder belongs
  // here, not on the card.
  it('shows a placeholder for each card while its first load is in flight', async () => {
    vi.mocked(fetchChartData).mockImplementation(() => new Promise<ChartData>(() => {}))
    const wrapper = await mountedBoard(THREE_CHARTS)
    expect(wrapper.findAll('[data-testid="spending-card-placeholder"]')).toHaveLength(3)
    expect(wrapper.find('[data-testid="spending-card"]').exists()).toBe(false)
  })

  it('caps its list request at the server maximum', async () => {
    vi.mocked(fetchFacetCounts).mockResolvedValue([])
    await mountedBoard([])
    expect(vi.mocked(listCharts).mock.calls[0]![0]).toBeLessThanOrEqual(100)
  })

  it('navigates to the workspace via the card name — the only route there from the board', async () => {
    vi.mocked(fetchChartData).mockImplementation((id) => Promise.resolve(emptyData(id)))
    const wrapper = await mountedBoard(THREE_CHARTS)
    const card = cardAt(wrapper, 0)
    await card.get('[data-testid="spending-card-name"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.fullPath).toBe('/charts/1')
  })

  it('renames a card and keeps the board in sync', async () => {
    vi.mocked(fetchChartData).mockImplementation((id) => Promise.resolve(emptyData(id)))
    const renamed: Chart = { ...THREE_CHARTS[0]!, name: 'Renamed chart' }
    vi.mocked(updateChart).mockResolvedValueOnce(renamed)
    const wrapper = await mountedBoard(THREE_CHARTS)
    const card = cardAt(wrapper, 0)
    await card.get('[data-testid="spending-card-menu"]').trigger('click')
    await card.get('[data-testid="spending-card-rename"]').trigger('click')
    await card.get('[data-testid="spending-card-rename-input"]').setValue('Renamed chart')
    await card.get('[data-testid="spending-card-rename-save"]').trigger('click')
    await flushPromises()

    expect(updateChart).toHaveBeenCalledWith(1, { name: 'Renamed chart' })
    expect(cardAt(wrapper, 0).get('[data-testid="spending-card-name"]').text()).toBe('Renamed chart')
  })

  it('deletes a card and removes it from the board, after confirming', async () => {
    vi.mocked(fetchChartData).mockImplementation((id) => Promise.resolve(emptyData(id)))
    vi.mocked(deleteChart).mockResolvedValue(undefined)
    const wrapper = await mountedBoard(THREE_CHARTS)
    const card = cardAt(wrapper, 0)
    await card.get('[data-testid="spending-card-menu"]').trigger('click')
    await card.get('[data-testid="spending-card-delete"]').trigger('click')
    // Armed, not yet deleted — the overflow item only arms the confirm step.
    expect(deleteChart).not.toHaveBeenCalled()
    await card.get('[data-testid="spending-card-delete-confirm"]').trigger('click')
    await flushPromises()
    expect(deleteChart).toHaveBeenCalledWith(1)
    expect(cards(wrapper)).toHaveLength(2)
  })

  it('shows an inline error on the card when delete fails, not a page banner', async () => {
    vi.mocked(fetchChartData).mockImplementation((id) => Promise.resolve(emptyData(id)))
    vi.mocked(deleteChart).mockRejectedValue(new ApiError(500, 'delete failed'))
    const wrapper = await mountedBoard(THREE_CHARTS)
    const card = cardAt(wrapper, 0)
    await card.get('[data-testid="spending-card-menu"]').trigger('click')
    await card.get('[data-testid="spending-card-delete"]').trigger('click')
    await card.get('[data-testid="spending-card-delete-confirm"]').trigger('click')
    await flushPromises()
    expect(cards(wrapper)).toHaveLength(3)
    expect(cardAt(wrapper, 0).text()).toContain('delete failed')
    expect(wrapper.find('[data-testid="board-banner"]').exists()).toBe(false)
  })

  it('moves a card up via the keyboard path', async () => {
    vi.mocked(fetchChartData).mockImplementation((id) => Promise.resolve(emptyData(id)))
    vi.mocked(updateChart).mockImplementation((id, patch) =>
      Promise.resolve({ ...THREE_CHARTS.find((c) => c.id === id)!, ...patch }),
    )
    const wrapper = await mountedBoard(THREE_CHARTS)
    const upButton = await moveUp(cardAt(wrapper, 1))
    await upButton.trigger('click')
    await flushPromises()
    const calls = vi
      .mocked(updateChart)
      .mock.calls.map((c) => [c[0], c[1].ordinal])
      .sort((a, b) => Number(a[0]) - Number(b[0]))
    expect(calls).toEqual([
      [1, 1],
      [2, 0],
    ])
  })

  it('disables move-up on the first card and move-down on the last', async () => {
    vi.mocked(fetchChartData).mockImplementation((id) => Promise.resolve(emptyData(id)))
    const wrapper = await mountedBoard(THREE_CHARTS)
    const first = cardAt(wrapper, 0)
    await first.get('[data-testid="spending-card-menu"]').trigger('click')
    expect(first.get('[data-testid="spending-card-move-up"]').attributes('disabled')).toBeDefined()

    const last = cardAt(wrapper, 2)
    await last.get('[data-testid="spending-card-menu"]').trigger('click')
    expect(last.get('[data-testid="spending-card-move-down"]').attributes('disabled')).toBeDefined()
  })

  it('shows a page-level error when the list itself fails to load', async () => {
    vi.mocked(listCharts).mockRejectedValue(new ApiError(500, 'list failed'))
    await router.push('/charts')
    await router.isReady()
    const wrapper = mount(SpendingBoardView, { global: { plugins: [router] } })
    await flushPromises()
    expect(wrapper.find('[data-testid="board-load-error"]').text()).toBe('list failed')
  })

  it('falls back to a generic message for a non-API failure, e.g. a dropped connection', async () => {
    vi.mocked(listCharts).mockRejectedValue(new Error('network down'))
    await router.push('/charts')
    await router.isReady()
    const wrapper = mount(SpendingBoardView, { global: { plugins: [router] } })
    await flushPromises()
    expect(wrapper.get('[data-testid="board-load-error"]').text()).toMatch(/connection/i)
  })

  it('drags a card down and persists the same ordinals the keyboard path would', async () => {
    // Spec §4.2: drag and the keyboard path call the SAME function. Rather
    // than reconstruct that from HTML5 drag events jsdom cannot fire, this
    // invokes the exact `onEnd` callback the board handed to `Sortable.create`
    // — that IS what a real drop calls — with the same "first card down" move
    // asserted for the keyboard path above, and expects the identical PATCHes.
    vi.mocked(fetchChartData).mockImplementation((id) => Promise.resolve(emptyData(id)))
    vi.mocked(updateChart).mockImplementation((id, patch) =>
      Promise.resolve({ ...THREE_CHARTS.find((c) => c.id === id)!, ...patch }),
    )
    await mountedBoard(THREE_CHARTS)
    expect(capturedSortableOptions).not.toBeNull()
    capturedSortableOptions!.onEnd({ oldIndex: 0, newIndex: 1 })
    await flushPromises()
    const calls = vi
      .mocked(updateChart)
      .mock.calls.map((c) => [c[0], c[1].ordinal])
      .sort((a, b) => Number(a[0]) - Number(b[0]))
    expect(calls).toEqual([
      [1, 1],
      [2, 0],
    ])
  })

  // Spec review round 2, finding N3: the card's name is a RouterLink
  // (spec review finding 5) living inside this whole-card drag surface (no
  // handle). Without excluding it, a drag begun on the name could start a
  // native HTML5 drag instead of SortableJS's own, and a drag ending on it
  // could still fire a click and navigate mid-reorder. This only proves the
  // OPTION reaches Sortable.create — no layer in this suite exercises a
  // real HTML5 drag gesture (jsdom cannot fire one, per the comment on
  // `capturedSortableOptions` above), so the actual exclusion behaviour
  // rests on reading SortableJS's own `filter` semantics, not execution.
  it("excludes the card name link from initiating a drag ('filter' passed to Sortable.create)", async () => {
    vi.mocked(fetchChartData).mockImplementation((id) => Promise.resolve(emptyData(id)))
    await mountedBoard(THREE_CHARTS)
    expect(capturedSortableOptions).not.toBeNull()
    expect(capturedSortableOptions!.filter).toBe('a')
  })

  it('ignores a drop with no index, and a drop back in the same slot persists nothing', async () => {
    vi.mocked(fetchChartData).mockImplementation((id) => Promise.resolve(emptyData(id)))
    await mountedBoard(THREE_CHARTS)
    expect(capturedSortableOptions).not.toBeNull()
    capturedSortableOptions!.onEnd({ oldIndex: null, newIndex: 1 })
    capturedSortableOptions!.onEnd({ oldIndex: 0, newIndex: undefined })
    capturedSortableOptions!.onEnd({ oldIndex: 0, newIndex: 0 })
    await flushPromises()
    expect(updateChart).not.toHaveBeenCalled()
  })

  // A failed reorder is rolled back, not left showing an order the server
  // rejected (spec review finding 9) — the board must not keep displaying
  // the optimistic move until the next full reload.
  it('shows a reorder error inline AND restores the previous order', async () => {
    vi.mocked(fetchChartData).mockImplementation((id) => Promise.resolve(emptyData(id)))
    vi.mocked(updateChart).mockRejectedValue(new ApiError(500, 'reorder failed'))
    const wrapper = await mountedBoard(THREE_CHARTS)
    const downButton = await moveDown(cardAt(wrapper, 0))
    await downButton.trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="board-reorder-error"]').text()).toBe('reorder failed')
    expect(cards(wrapper)).toHaveLength(3)
    const names = cards(wrapper).map((c) => c.get('[data-testid="spending-card-name"]').text())
    expect(names).toEqual(['Chart 1', 'Chart 2', 'Chart 3'])
  })

  it('destroys the drag instance when the last card is deleted and the grid disappears', async () => {
    const one = [chart({ id: 1, name: 'Only chart', ordinal: 0 })]
    vi.mocked(fetchChartData).mockImplementation((id) => Promise.resolve(emptyData(id)))
    vi.mocked(deleteChart).mockResolvedValue(undefined)
    vi.mocked(fetchFacetCounts).mockResolvedValue([])
    const wrapper = await mountedBoard(one)
    expect(capturedSortableOptions).not.toBeNull()
    sortableDestroy.mockClear()

    const card = cardAt(wrapper, 0)
    await card.get('[data-testid="spending-card-menu"]').trigger('click')
    await card.get('[data-testid="spending-card-delete"]').trigger('click')
    await card.get('[data-testid="spending-card-delete-confirm"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="spending-empty-state"]').exists()).toBe(true)
    expect(sortableDestroy).toHaveBeenCalled()
  })

  it('destroys the drag instance on unmount', async () => {
    vi.mocked(fetchChartData).mockImplementation((id) => Promise.resolve(emptyData(id)))
    const wrapper = await mountedBoard(THREE_CHARTS)
    expect(capturedSortableOptions).not.toBeNull()
    sortableDestroy.mockClear()
    wrapper.unmount()
    expect(sortableDestroy).toHaveBeenCalled()
  })

  // --- Currency picker (Task 9 fix round 1) -------------------------------
  //
  // Task 8's brief: the board "supplies [the currency] from the existing
  // `useCurrencyOptions()` composable ... and lets it be changed through the
  // existing `CurrencySelect.vue`". Without this wiring every chart the board
  // creates — the empty state's "All spending" and every saved draft — is
  // created with a hardcoded `EUR`, silently wrong on a non-EUR archive.

  it('renders the currency picker in the header controls and defaults to the first available option', async () => {
    vi.mocked(fetchFacetCounts).mockResolvedValue([])
    const wrapper = await mountedBoard([])
    const controls = wrapper.get('[data-testid="page-header-controls"]')
    const select = controls.get('[data-testid="currency-select"]').element as HTMLSelectElement
    expect(select.value).toBe('EUR')
  })

  it('changing the currency passes the new value through to both child components', async () => {
    vi.mocked(fetchFacetCounts).mockResolvedValue([])
    const wrapper = await mountedBoard([])
    await wrapper.get('[data-testid="currency-select"]').setValue('GBP')
    await flushPromises()
    expect(wrapper.getComponent(QuestionDraft).props('currency')).toBe('GBP')
    expect(wrapper.getComponent(SpendingEmptyState).props('currency')).toBe('GBP')
  })

  it('persists the chosen currency across a remount', async () => {
    vi.mocked(fetchFacetCounts).mockResolvedValue([])
    const first = await mountedBoard([])
    await first.get('[data-testid="currency-select"]').setValue('GBP')
    await flushPromises()
    first.unmount()

    const second = await mountedBoard([])
    const select = second.get('[data-testid="currency-select"]').element as HTMLSelectElement
    expect(select.value).toBe('GBP')
  })

  it('saves a chart with the chosen currency, not a hardcoded default', async () => {
    // This is the assertion that actually pins the defect: a board that
    // hardcodes the currency it hands to its children would call
    // `createChart` with `display_currency: 'EUR'` here regardless of what
    // the picker shows.
    vi.mocked(fetchFacetCounts).mockResolvedValue([])
    const created = chart({
      id: 9,
      name: 'All spending',
      ordinal: 0,
      default_split: 'category',
      display_currency: 'GBP',
    })
    vi.mocked(createChart).mockResolvedValue(created)
    vi.mocked(fetchChartData).mockResolvedValue(emptyData(9))

    const wrapper = await mountedBoard([])
    await wrapper.get('[data-testid="currency-select"]').setValue('GBP')
    await flushPromises()

    await wrapper.get('[data-testid="spending-empty-proposal"]').trigger('click')
    await flushPromises()

    expect(createChart).toHaveBeenCalledWith(expect.objectContaining({ display_currency: 'GBP' }))
  })
})
