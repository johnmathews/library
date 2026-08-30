import { describe, expect, it, vi, beforeEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import DrillCellBody from '../DrillCellBody.vue'
import FacetEditor from '@/components/facets/FacetEditor.vue'
import { cellArgs, type CellBody, type ChartArgs, type ChartData, type Footer } from '@/api/spending'
import { ApiError } from '@/api/client'
import { toCents, fromCents } from '@/spending/money'

const fetchCell = vi.fn()
vi.mock('@/api/spending', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/spending')>()),
  fetchCell: (...args: unknown[]) => fetchCell(...args),
}))

const fetchFacets = vi.fn()
const fetchDocumentLabels = vi.fn()
vi.mock('@/api/facets', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/facets')>()),
  fetchFacets: (...args: unknown[]) => fetchFacets(...args),
  fetchDocumentLabels: (...args: unknown[]) => fetchDocumentLabels(...args),
}))

// PaymentGroup renders nothing when its own document is alone in its
// payment group — return a single-document group for every id so it stays
// out of the way of this component's own assertions.
const fetchPayment = vi.fn()
vi.mock('@/api/payments', () => ({
  fetchPayment: (...args: unknown[]) => fetchPayment(...args),
  splitPayment: vi.fn(),
  mergePayment: vi.fn(),
}))

// PaymentGroup and this component both link to `/documents/:id` via
// RouterLink; stub it the way PaymentGroup.spec.ts does so mounting outside
// a router doesn't throw.
const RouterLinkStub = {
  props: ['to'],
  template: '<a :href="to"><slot /></a>',
}

const BASE_FOOTER: Footer = {
  netted_refunds: '0.00',
  refund_count: 0,
  excluded: [],
  unclassified: null,
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
  until: '2026-08-31',
  cells: [],
  splits: [],
  total: '0.00',
  payments: 0,
  documents: 0,
  footer: BASE_FOOTER,
}

const CELL_WITH_MERGE: CellBody = {
  period: '2026-08-01',
  split_value: 'hosting',
  total: '500.00',
  label: 'Hosting',
  colour: null,
  payments: [
    {
      payment_id: 1,
      total: '500.00',
      documents: [
        {
          id: 101,
          title: 'Northwind hosting invoice',
          date: '2026-08-04',
          amount: '500.00',
          currency: 'EUR',
          amount_kind: 'payment_due',
          reference: 'INV-4471',
          is_canonical: true,
        },
        {
          id: 102,
          title: 'Northwind hosting receipt',
          date: '2026-08-05',
          amount: '500.00',
          currency: 'EUR',
          amount_kind: 'payment_made',
          reference: 'RCT-8820',
          is_canonical: false,
        },
      ],
    },
  ],
}

const CELL_WITH_AMOUNTLESS_DOCUMENT: CellBody = {
  period: '2026-08-01',
  split_value: 'hosting',
  total: '300.00',
  label: 'Hosting',
  colour: null,
  payments: [
    {
      payment_id: 2,
      total: '300.00',
      documents: [
        {
          id: 201,
          title: 'Meridian hosting invoice',
          date: '2026-08-11',
          amount: '300.00',
          currency: 'EUR',
          amount_kind: 'payment_due',
          reference: 'INV-9910',
          is_canonical: true,
        },
        {
          id: 202,
          title: 'Meridian hand-merged note',
          date: null,
          amount: null,
          currency: null,
          amount_kind: null,
          reference: null,
          is_canonical: false,
        },
      ],
    },
  ],
}

function mountCellBody(
  overrides: Partial<{
    chartId: number
    period: string
    splitValue: string | null
    args: ChartArgs
    chartName: string
  }> = {},
): VueWrapper {
  return mount(DrillCellBody, {
    attachTo: document.body,
    props: {
      chartId: 7,
      period: '2026-08-01',
      splitValue: 'hosting',
      args: cellArgs(DATA),
      chartName: 'All spending',
      ...overrides,
    },
    global: { stubs: { RouterLink: RouterLinkStub } },
  })
}

async function mountedCellBody(cell: CellBody): Promise<VueWrapper> {
  fetchCell.mockResolvedValue(cell)
  const wrapper = mountCellBody()
  await flushPromises()
  return wrapper
}

function paymentTotals(wrapper: VueWrapper): string[] {
  return wrapper
    .findAll('[data-testid="drill-payment-total"]')
    .map((row) => row.attributes('data-amount')!)
}

beforeEach(() => {
  fetchCell.mockReset()
  fetchFacets.mockReset().mockResolvedValue([])
  fetchDocumentLabels.mockReset().mockResolvedValue({})
  fetchPayment.mockReset().mockImplementation((id: number) =>
    Promise.resolve({
      payment_id: id,
      documents: [{ id, title: null, document_date: null, amount_kind: null, reference: null }],
    }),
  )
})

describe('DrillCellBody', () => {
  it("sends /data resolved arguments plus the cell period, verbatim", async () => {
    fetchCell.mockResolvedValue(CELL_WITH_MERGE)
    mountCellBody({ args: cellArgs(DATA), period: '2026-08-01', splitValue: 'hosting' })
    await flushPromises()
    expect(fetchCell).toHaveBeenCalledWith(7, '2026-08-01', 'hosting', {
      grain: 'month',
      split: 'category',
      currency: 'EUR',
      from: '2026-01-01',
      to: '2026-08-31',
    })
  })

  // The server's 422 names the correct boundary. An empty panel does not.
  it('renders the 422 detail rather than an empty panel', async () => {
    fetchCell.mockRejectedValue(
      new ApiError(422, 'period 2026-08-15 is not the start of a month; use 2026-08-01'),
    )
    const wrapper = mountCellBody()
    await flushPromises()
    expect(wrapper.text()).toContain('use 2026-08-01')
    expect(wrapper.find('[data-testid="drill-empty"]').exists()).toBe(false)
  })

  // The panel is where a wrong merge is noticed, so it must add up to the bar.
  it('shows each payment total and their sum equals the cell total', async () => {
    const wrapper = await mountedCellBody(CELL_WITH_MERGE)
    const shown = paymentTotals(wrapper)
      .map(toCents)
      .reduce((a, b) => a + b, 0)
    expect(fromCents(shown)).toBe(CELL_WITH_MERGE.total)
  })

  // A merged pair doubles the document sum; that is the merge this panel exposes.
  it('never presents a sum of document amounts as the total', async () => {
    const wrapper = await mountedCellBody(CELL_WITH_MERGE) // documents sum to 2x the total
    expect(wrapper.text()).not.toContain(fromCents(2 * toCents(CELL_WITH_MERGE.total)))
  })

  // A hand-made MERGE override can pull an amountless document into a group.
  it('renders a document with no amount and no currency', async () => {
    const wrapper = await mountedCellBody(CELL_WITH_AMOUNTLESS_DOCUMENT)
    expect(wrapper.text()).toContain('No amount recorded')
  })

  it('reuses FacetEditor and PaymentGroup per document rather than reimplementing them', async () => {
    const wrapper = await mountedCellBody(CELL_WITH_MERGE)
    expect(wrapper.findAll('[data-testid="facet-editor"]')).toHaveLength(2)
    // fetchDocumentLabels is called per rendered document (best-effort labels).
    expect(fetchDocumentLabels).toHaveBeenCalledWith(101)
    expect(fetchDocumentLabels).toHaveBeenCalledWith(102)
    // fetchPayment (PaymentGroup's own load) is likewise called per document.
    expect(fetchPayment).toHaveBeenCalledWith(101)
    expect(fetchPayment).toHaveBeenCalledWith(102)
  })

  it('shows a loading state while the fetch is in flight', async () => {
    fetchCell.mockReturnValue(new Promise(() => {})) // never resolves
    const wrapper = mountCellBody()
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-testid="drill-loading"]').exists()).toBe(true)
  })

  it('falls back to an empty label map when fetching a document’s labels fails', async () => {
    fetchDocumentLabels.mockReset().mockRejectedValue(new Error('boom'))
    const wrapper = await mountedCellBody(CELL_WITH_AMOUNTLESS_DOCUMENT)
    // The best-effort fallback still lets FacetEditor render (empty labels)
    // rather than the whole body failing.
    expect(wrapper.findAll('[data-testid="facet-editor"]')).toHaveLength(2)
  })

  it('adopts a saved label map from the facet editor for that document', async () => {
    const wrapper = await mountedCellBody(CELL_WITH_MERGE)
    const editor = wrapper.findAllComponents(FacetEditor)[0]!
    editor.vm.$emit('saved', { category: 'hosting' })
    await flushPromises()
    expect(editor.props('labels')).toEqual({ category: 'hosting' })
  })

  it('renders a real empty state when the cell genuinely has no payments', async () => {
    const wrapper = await mountedCellBody({
      period: '2026-08-01',
      split_value: null,
      total: '0.00',
      label: '',
      colour: null,
      payments: [],
    })
    expect(wrapper.find('[data-testid="drill-empty"]').exists()).toBe(true)
  })
})
