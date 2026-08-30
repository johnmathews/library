import { beforeAll, beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'

// This repository is public — every chart name, facet value and amount
// below is invented.

vi.mock('vue-chartjs', () => ({
  Bar: { name: 'Bar', props: ['data', 'options'], template: '<canvas />' },
}))

const fetchChart = vi.fn()
const fetchChartData = vi.fn()
const fetchCell = vi.fn()
const fetchFooterBucket = vi.fn()
const listCharts = vi.fn()
vi.mock('@/api/spending', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/spending')>()),
  fetchChart: (...args: unknown[]) => fetchChart(...args),
  fetchChartData: (...args: unknown[]) => fetchChartData(...args),
  fetchCell: (...args: unknown[]) => fetchCell(...args),
  fetchFooterBucket: (...args: unknown[]) => fetchFooterBucket(...args),
  listCharts: (...args: unknown[]) => listCharts(...args),
}))

const fetchFacets = vi.fn()
const fetchDocumentLabels = vi.fn()
vi.mock('@/api/facets', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/facets')>()),
  fetchFacets: (...args: unknown[]) => fetchFacets(...args),
  fetchDocumentLabels: (...args: unknown[]) => fetchDocumentLabels(...args),
}))

// DrillCellBody renders a PaymentGroup per document, which fetches its own
// payment group — a single-document group renders nothing (PaymentGroup's
// own contract), which keeps this suite's assertions about the drill body
// itself uncluttered by that component's markup.
const fetchPayment = vi.fn()
vi.mock('@/api/payments', () => ({
  fetchPayment: (...args: unknown[]) => fetchPayment(...args),
  splitPayment: vi.fn(),
  mergePayment: vi.fn(),
}))

import {
  cellArgs,
  type Cell,
  type Chart,
  type ChartData,
  type Footer,
  type SplitValue,
} from '@/api/spending'
import { ApiError } from '@/api/client'
import { OTHER_VALUE } from '@/spending/palette'
import SpendingWorkspaceView from '../SpendingWorkspaceView.vue'
import SpendingChart from '@/components/spending/SpendingChart.vue'

/**
 * jsdom implements HTMLDialogElement's `open` property only —
 * `showModal()`/`close()` are missing — same stub as
 * SpendingDrillPanel.spec.ts.
 */
beforeAll(() => {
  if (typeof HTMLDialogElement.prototype.showModal !== 'function') {
    HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) {
      this.setAttribute('open', '')
    }
  }
  if (typeof HTMLDialogElement.prototype.close !== 'function') {
    HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) {
      this.removeAttribute('open')
      this.dispatchEvent(new Event('close'))
    }
  }
})

// jsdom has no ResizeObserver at all. The workspace's own presentation
// decision (side panel vs bottom sheet) is driven entirely by this
// observer's callback, never by window.innerWidth/matchMedia — this stub
// lets a test drive that callback directly, exactly as a real browser would
// after the content column resizes, without needing a real layout engine.
let resizeCallback: ResizeObserverCallback | null = null
const resizeObserve = vi.fn()
const resizeDisconnect = vi.fn()
class FakeResizeObserver {
  constructor(cb: ResizeObserverCallback) {
    resizeCallback = cb
  }
  observe = resizeObserve
  unobserve = vi.fn()
  disconnect = resizeDisconnect
}
vi.stubGlobal('ResizeObserver', FakeResizeObserver)

function fireResize(width: number): void {
  resizeCallback?.(
    [{ contentRect: { width } } as ResizeObserverEntry],
    {} as ResizeObserver,
  )
}

// --- Fixtures ----------------------------------------------------------

function chart(overrides: Partial<Chart> = {}): Chart {
  return {
    id: 7,
    name: 'Software spending',
    question_text: 'How much do we spend on software?',
    rule: { all: [] },
    default_grain: 'month',
    default_split: 'category',
    display_currency: 'EUR',
    ordinal: 0,
    ...overrides,
  }
}

// Seven split values so `bands()` folds the smallest (misc, 50.00) into
// Other — MAX_BANDS is six (SPLIT_PALETTE's length) — which is what the
// "opens the Other body" test needs, while `hosting` (700.00, the largest)
// stays a real, individually-clickable band for the isolate/headline tests.
const SPLITS: SplitValue[] = [
  { value: 'hosting', label: 'Hosting', colour: null },
  { value: 'design', label: 'Design', colour: null },
  { value: 'support', label: 'Support', colour: null },
  { value: 'storage', label: 'Storage', colour: null },
  { value: 'backup', label: 'Backup', colour: null },
  { value: 'monitoring', label: 'Monitoring', colour: null },
  { value: 'misc', label: 'Misc', colour: null },
]

function labelFor(value: string): string {
  return SPLITS.find((s) => s.value === value)!.label
}

const CELLS: Cell[] = [
  { period: '2026-01-01', split_value: 'hosting', total: '700.00', payments: 1 },
  { period: '2026-01-01', split_value: 'design', total: '600.00', payments: 1 },
  { period: '2026-01-01', split_value: 'support', total: '500.00', payments: 1 },
  { period: '2026-01-01', split_value: 'storage', total: '400.00', payments: 1 },
  { period: '2026-01-01', split_value: 'backup', total: '300.00', payments: 1 },
  { period: '2026-01-01', split_value: 'monitoring', total: '200.00', payments: 1 },
  { period: '2026-01-01', split_value: 'misc', total: '50.00', payments: 1 },
]

const FOOTER: Footer = {
  netted_refunds: '0.00',
  refund_count: 0,
  excluded: [],
  unclassified: { amount_kind: 'unclassified', amount: '10.00', documents: 2 },
  uncategorised: null,
  undated: null,
  unaccounted: null,
  unconvertible: [],
}

const DATA: ChartData = {
  chart_id: 7,
  grain: 'month',
  split: 'category',
  currency: 'EUR',
  since: '2026-01-01',
  until: '2026-01-31',
  cells: CELLS,
  splits: SPLITS,
  total: '2750.00',
  payments: 7,
  documents: 7,
  footer: FOOTER,
}

const router = createRouter({
  history: createMemoryHistory(),
  routes: [
    { path: '/charts', name: 'charts', component: { template: '<div/>' } },
    { path: '/charts/:chartId', name: 'spending-workspace', component: SpendingWorkspaceView },
  ],
})

async function mountedWorkspace(chartId = 7): Promise<VueWrapper> {
  await router.push(`/charts/${chartId}`)
  await router.isReady()
  const wrapper = mount(SpendingWorkspaceView, { global: { plugins: [router] } })
  await flushPromises()
  return wrapper
}

async function changeRange(wrapper: VueWrapper, from: string, to: string): Promise<void> {
  await wrapper.get('[data-testid="workspace-from"]').setValue(from)
  await wrapper.get('[data-testid="workspace-to"]').setValue(to)
  await flushPromises()
}

async function selectSplit(wrapper: VueWrapper, value: string): Promise<void> {
  await wrapper.get('[data-testid="workspace-split"]').setValue(value)
  await flushPromises()
}

async function clickCell(wrapper: VueWrapper, period: string, splitValue: string | null | typeof OTHER_VALUE): Promise<void> {
  wrapper.findComponent(SpendingChart).vm.$emit('cell', period, splitValue)
  await flushPromises()
}

async function isolate(wrapper: VueWrapper, value: string): Promise<void> {
  const label = labelFor(value)
  const row = wrapper.findAll('[data-testid="spending-legend-row"]').find((r) => r.text().includes(label))
  if (!row) throw new Error(`no legend row for ${label}`)
  await row.trigger('click')
}

function headlineTotal(wrapper: VueWrapper): string {
  return wrapper.get('[data-testid="workspace-headline-figure"]').text()
}

function selectionLine(wrapper: VueWrapper) {
  return wrapper.get('[data-testid="workspace-selection"]')
}

describe('SpendingWorkspaceView', () => {
  beforeEach(() => {
    fetchChart.mockResolvedValue(chart())
    fetchChartData.mockResolvedValue(DATA)
    fetchFacets.mockResolvedValue([])
    fetchDocumentLabels.mockResolvedValue({})
    fetchPayment.mockResolvedValue({
      payment_id: 1,
      documents: [{ id: 1, title: null, document_date: null, amount_kind: null, reference: null }],
    })
    fetchCell.mockResolvedValue({
      period: '2026-01-01',
      split_value: 'hosting',
      total: '700.00',
      payments: [],
      label: 'Hosting',
      colour: null,
    })
    fetchFooterBucket.mockResolvedValue({ bucket: 'unclassified', total: 2, documents: [] })
  })

  afterEach(() => {
    vi.clearAllMocks()
    resizeCallback = null
  })

  it('sends the toolbar through PageHeader controls, not a band of its own', async () => {
    const wrapper = await mountedWorkspace()
    const controls = wrapper.get('[data-testid="page-header-controls"]')
    expect(controls.find('[data-testid="workspace-toolbar"]').exists()).toBe(true)
  })

  // The panel's presentation follows the CONTENT COLUMN, not the viewport: at
  // a 1280px viewport the column is 960px expanded and 1136px collapsed —
  // both cases are driven here purely through the mocked ResizeObserver
  // callback, never through window.innerWidth, so the SAME jsdom viewport
  // produces both outcomes depending only on what the observer reports.
  it('opens the panel as a sheet when the content column is below the threshold', async () => {
    const wrapper = await mountedWorkspace()
    fireResize(600)
    await flushPromises()
    await clickCell(wrapper, '2026-01-01', 'hosting')
    expect(wrapper.get('[data-testid="drill-panel"]').attributes('data-presentation')).toBe('sheet')
  })

  it('opens it as a side panel when the column is above it, at the same viewport', async () => {
    const wrapper = await mountedWorkspace()
    fireResize(960)
    await flushPromises()
    await clickCell(wrapper, '2026-01-01', 'hosting')
    expect(wrapper.get('[data-testid="drill-panel"]').attributes('data-presentation')).toBe('panel')
  })

  it('loads the chart by id rather than paging the list', async () => {
    await mountedWorkspace()
    expect(fetchChart).toHaveBeenCalledWith(7)
    expect(listCharts).not.toHaveBeenCalled()
  })

  it('refetches when a toolbar control changes, and never clamps the axis instead', async () => {
    // §10.3 #2: the range filters the data, so the headline and the
    // drawing can never disagree — `from`/`to` go to the API.
    const wrapper = await mountedWorkspace()
    await changeRange(wrapper, '2026-03-01', '2026-06-30')
    expect(fetchChartData.mock.calls.at(-1)![1]).toMatchObject({ from: '2026-03-01', to: '2026-06-30' })
  })

  it('sends split= when the split is turned off, so the default does not return', async () => {
    const wrapper = await mountedWorkspace()
    await selectSplit(wrapper, '')
    expect(fetchChartData.mock.calls.at(-1)![1]).toMatchObject({ split: '' })
  })

  it('opens the panel on a bar click with /data echoed arguments', async () => {
    const wrapper = await mountedWorkspace()
    await clickCell(wrapper, '2026-01-01', 'hosting')
    expect(wrapper.get('[data-testid="drill-panel"]').attributes('open')).toBeDefined()
    expect(wrapper.find('[data-testid="drill-cell-body"]').exists()).toBe(true)
    expect(fetchCell).toHaveBeenCalledWith(7, '2026-01-01', 'hosting', cellArgs(DATA))
  })

  it('opens the Other body for the folded segment, not a /cell call', async () => {
    const wrapper = await mountedWorkspace()
    await clickCell(wrapper, '2026-01-01', OTHER_VALUE)
    expect(wrapper.find('[data-testid="drill-other-body"]').exists()).toBe(true)
    expect(fetchCell).not.toHaveBeenCalled()
  })

  it('excludes a single band via a modifier-click, without hiding the rest', async () => {
    const wrapper = await mountedWorkspace()
    const row = wrapper
      .findAll('[data-testid="spending-legend-row"]')
      .find((r) => r.text().includes(labelFor('hosting')))!
    await row.trigger('click', { metaKey: true })
    // Two or more bands still visible, so the selection line names what is
    // HIDDEN rather than what is showing — the branch `isolate` above does
    // not exercise.
    expect(selectionLine(wrapper).text()).toContain('Hiding')
    expect(selectionLine(wrapper).text()).toContain('Hosting')

    // Clicking the SAME row's exclude toggle again un-hides it.
    await row.trigger('click', { metaKey: true })
    expect(wrapper.find('[data-testid="workspace-selection"]').exists()).toBe(false)
  })

  it('picks a folded member from the Other body and opens its own cell drill', async () => {
    const wrapper = await mountedWorkspace()
    await clickCell(wrapper, '2026-01-01', OTHER_VALUE)
    expect(wrapper.find('[data-testid="drill-other-body"]').exists()).toBe(true)

    await wrapper.get('[data-testid="drill-other-row"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="drill-cell-body"]').exists()).toBe(true)
    expect(fetchCell).toHaveBeenCalledWith(7, '2026-01-01', 'misc', cellArgs(DATA))
  })

  it('closes the panel when the drill shell asks to', async () => {
    const wrapper = await mountedWorkspace()
    await clickCell(wrapper, '2026-01-01', 'hosting')
    expect(wrapper.get('[data-testid="drill-panel"]').attributes('open')).toBeDefined()

    await wrapper.get('[data-testid="drill-close"]').trigger('click')
    expect(wrapper.get('[data-testid="drill-panel"]').attributes('open')).toBeUndefined()
  })

  it('changing the currency control refetches with the new currency', async () => {
    const wrapper = await mountedWorkspace()
    await wrapper.get('[data-testid="workspace-currency-select"] [data-testid="currency-select"]').setValue('GBP')
    await flushPromises()
    expect(fetchChartData.mock.calls.at(-1)![1]).toMatchObject({ currency: 'GBP' })
  })

  it('disconnects the resize observer on unmount', async () => {
    const wrapper = await mountedWorkspace()
    expect(resizeObserve).toHaveBeenCalled()
    wrapper.unmount()
    expect(resizeDisconnect).toHaveBeenCalled()
  })

  it('shows an error rather than fetching an unparseable chart id', async () => {
    await router.push('/charts/not-a-number')
    await router.isReady()
    const wrapper = mount(SpendingWorkspaceView, { global: { plugins: [router] } })
    await flushPromises()
    expect(wrapper.get('[data-testid="workspace-load-error"]').text()).toBe('Unknown chart.')
    expect(fetchChart).not.toHaveBeenCalled()
  })

  it('opens a footer bucket in the same panel shell', async () => {
    const wrapper = await mountedWorkspace()
    await wrapper.get('[data-testid="spending-footer-bucket-unclassified"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="drill-panel"]').attributes('open')).toBeDefined()
    expect(wrapper.find('[data-testid="drill-bucket-body"]').exists()).toBe(true)
    expect(fetchFooterBucket).toHaveBeenCalledWith(7, 'unclassified', expect.anything())
  })

  // Carried from Task 3: SpendingChart has no loading signal of its own and
  // never keys its <Bar> on `data`, so the previous render is held rather
  // than flashing a skeleton. The CONSUMER owns the reduced-opacity
  // treatment, and SpendingCard already does it — the workspace must too.
  it('dims the chart while a refetch is in flight, without unmounting it', async () => {
    const wrapper = await mountedWorkspace()
    // The underlying DOM node — never `.vm`, which vue-test-utils re-wraps
    // fresh on every `findComponent()` call and so is never `Object.is`-
    // stable across two calls even when the component was never unmounted.
    const elementBefore = wrapper.findComponent(SpendingChart).element

    let resolveFetch: (value: ChartData) => void = () => {}
    fetchChartData.mockImplementation(
      () =>
        new Promise<ChartData>((resolve) => {
          resolveFetch = resolve
        }),
    )
    await wrapper.get('[data-testid="workspace-grain"]').setValue('year')
    await flushPromises()

    expect(wrapper.get('[data-testid="workspace-chart-region"]').attributes('data-busy')).toBe('true')
    expect(wrapper.findComponent(SpendingChart).exists()).toBe(true)
    expect(wrapper.findComponent(SpendingChart).element).toBe(elementBefore)

    resolveFetch(DATA)
    await flushPromises()
    expect(wrapper.get('[data-testid="workspace-chart-region"]').attributes('data-busy')).toBe('false')
  })

  // Carried from Task 4: the legend emits `reset` from its "Show all"
  // control when any band is hidden. An unwired emit is a dead control.
  it('restores every hidden band when the legend asks to reset', async () => {
    const wrapper = await mountedWorkspace()
    await isolate(wrapper, 'hosting')
    expect(wrapper.find('[data-testid="workspace-selection"]').exists()).toBe(true)
    await wrapper.get('[data-testid="spending-legend-reset"]').trigger('click')
    expect(wrapper.find('[data-testid="workspace-selection"]').exists()).toBe(false)
  })

  // §4.7: isolation must not touch the number the API reported.
  it('keeps the headline total when a legend entry is isolated', async () => {
    const wrapper = await mountedWorkspace()
    const before = headlineTotal(wrapper)
    await isolate(wrapper, 'hosting')
    expect(headlineTotal(wrapper)).toBe(before)
    expect(selectionLine(wrapper).text()).toContain('Hosting')
  })

  // --- Extra coverage: error surfaces, route changes, the mobile chip -----

  it('shows an inline error when the chart itself fails to load', async () => {
    fetchChart.mockRejectedValue(new ApiError(404, 'No chart with that id.'))
    const wrapper = await mountedWorkspace()
    expect(wrapper.get('[data-testid="workspace-load-error"]').text()).toBe('No chart with that id.')
    expect(wrapper.find('[data-testid="workspace-toolbar"]').exists()).toBe(false)
  })

  it('falls back to a generic message for a non-API chart-load failure', async () => {
    fetchChart.mockRejectedValue(new Error('network down'))
    const wrapper = await mountedWorkspace()
    expect(wrapper.get('[data-testid="workspace-load-error"]').text()).toMatch(/connection/i)
  })

  it('shows an inline data error without losing the toolbar', async () => {
    fetchChartData.mockRejectedValue(new ApiError(500, 'could not compute'))
    const wrapper = await mountedWorkspace()
    expect(wrapper.get('[data-testid="workspace-data-error"]').text()).toBe('could not compute')
    expect(wrapper.find('[data-testid="workspace-toolbar"]').exists()).toBe(true)
  })

  it('reloads the chart when the route id changes', async () => {
    await mountedWorkspace(7)
    fetchChart.mockResolvedValue(chart({ id: 9, name: 'Travel spending' }))
    fetchChartData.mockResolvedValue({ ...DATA, chart_id: 9 })
    await router.push('/charts/9')
    await flushPromises()
    expect(fetchChart).toHaveBeenLastCalledWith(9)
  })

  // frontend-view-principles.md §4: a wide-only presentation must not drop a
  // capability. The chip is a real control, not a static label — tapping it
  // keeps every toolbar control reachable.
  it('keeps every toolbar control reachable via the chip below the threshold', async () => {
    const wrapper = await mountedWorkspace()
    await wrapper.get('[data-testid="workspace-toolbar-chip-button"]').trigger('click')
    await wrapper.get('[data-testid="workspace-grain"]').setValue('year')
    await flushPromises()
    expect(fetchChartData.mock.calls.at(-1)![1]).toMatchObject({ grain: 'year' })
  })
})
