import { describe, it, expect } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import SpendingFooter from '../SpendingFooter.vue'
import type { ChartData, Footer } from '@/api/spending'

// A footer with nothing in any of its optional slots — the "nothing was
// excluded" case, distinct from a slot that is simply absent from the
// fixture (spec §4.5's absent-vs-empty distinction).
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

function chartData(footer: Partial<Footer>, overrides: Partial<ChartData> = {}): ChartData {
  return {
    chart_id: 1,
    grain: 'month',
    split: null,
    currency: 'GBP',
    since: null,
    until: null,
    cells: [],
    splits: [],
    total: '1250.00',
    payments: 14,
    documents: 16,
    footer: { ...BASE_FOOTER, ...footer },
    ...overrides,
  }
}

const WITH_REFUND = chartData({
  refund_count: 1,
  netted_refunds: '45.00',
})

const FULL = chartData({
  refund_count: 1,
  netted_refunds: '45.00',
  excluded: [{ amount_kind: 'coverage_limit', amount: '210.00', documents: 3 }],
  unclassified: { amount_kind: 'unclassified', amount: '80.00', documents: 2 },
  uncategorised: { amount_kind: 'uncategorised', amount: '65.00', documents: 4 },
  unconvertible: [{ currency: 'JPY', amount: '3000.00', documents: 1 }],
})

const WITH_UNACCOUNTED = chartData({
  unaccounted: { amount_kind: 'unaccounted', amount: '12.00', documents: 1 },
})

const ZERO_NET_UNCONVERTIBLE = chartData({
  unconvertible: [{ currency: 'CHF', amount: '0.00', documents: 2 }],
})

const WITH_NULL_CURRENCY = chartData({
  unconvertible: [
    { currency: 'CHF', amount: '30.00', documents: 1 },
    { currency: null, amount: '10.00', documents: 1 },
    { currency: 'AUD', amount: '20.00', documents: 1 },
  ],
})

const EMPTY_FOOTER = chartData({})

function mountFooter(data: ChartData): VueWrapper {
  return mount(SpendingFooter, { props: { data } })
}

// `find`, not `get`: the "renders every block even when null" test asserts
// `.exists()`, which `get` (throw-if-missing) does not type.
function headerBlock(wrapper: VueWrapper) {
  return wrapper.find('[data-testid="spending-footer-header"]')
}
function excludedBlock(wrapper: VueWrapper) {
  return wrapper.find('[data-testid="spending-footer-excluded"]')
}
function attentionBlock(wrapper: VueWrapper) {
  return wrapper.find('[data-testid="spending-footer-attention"]')
}
function unconvertibleRows(wrapper: VueWrapper) {
  return wrapper.findAll('[data-testid="spending-footer-unconvertible-row"]')
}
function unconvertibleRow(wrapper: VueWrapper) {
  return wrapper.get('[data-testid="spending-footer-unconvertible-row"]')
}
function refundFigure(wrapper: VueWrapper) {
  return wrapper.get('[data-testid="spending-footer-refund-figure"]')
}
function unconvertibleFigure(wrapper: VueWrapper) {
  return wrapper.get('[data-testid="spending-footer-unconvertible-documents"]')
}
function unconvertibleAmount(wrapper: VueWrapper) {
  return wrapper.get('[data-testid="spending-footer-unconvertible-amount"]')
}
function bucketButton(wrapper: VueWrapper, token: string) {
  return wrapper.get(`[data-testid="spending-footer-bucket-${token}"]`)
}

describe('SpendingFooter', () => {
  // A refund is IN the total and lowers it. Under "excluded from the total"
  // it would read as money the chart ignored — the opposite of what happened.
  it('puts netted refunds in the header block, never under excluded', () => {
    const wrapper = mountFooter(WITH_REFUND)
    expect(headerBlock(wrapper).text()).toContain('1 refund')
    expect(excludedBlock(wrapper).text()).not.toContain('refund')
  })

  // Excluded means correctly not spending; undecided means not yet decided.
  it('puts unclassified and uncategorised under needs attention', () => {
    const wrapper = mountFooter(FULL)
    expect(attentionBlock(wrapper).text()).toContain('unclassified')
    expect(attentionBlock(wrapper).text()).toContain('uncategorised')
    expect(excludedBlock(wrapper).text()).not.toContain('uncategorised')
  })

  it('renders unaccounted under needs attention when it is not empty', () => {
    // It should always be empty. When it is not, this is the money in the hole.
    expect(attentionBlock(mountFooter(WITH_UNACCOUNTED)).text()).toContain('unaccounted')
  })

  // An unconvertible payment and an equal unconvertible refund net to 0.00
  // across two documents, which without the count reads as "nothing missing".
  it('always renders documents beside an unconvertible amount', () => {
    const row = unconvertibleRow(mountFooter(ZERO_NET_UNCONVERTIBLE))
    expect(row.text()).toContain('0.00')
    expect(row.text()).toContain('2 documents')
  })

  it('labels a null unconvertible currency and sorts it last', () => {
    const rows = unconvertibleRows(mountFooter(WITH_NULL_CURRENCY))
    expect(rows).toHaveLength(3)
    expect(rows.at(-1)!.text()).toContain('No currency')
  })

  // §2.4: these two have no bucket route. Wiring them up is a 422.
  it('renders refund_count and unconvertible documents as plain figures', () => {
    const wrapper = mountFooter(FULL)
    expect(refundFigure(wrapper).element.tagName).not.toBe('BUTTON')
    expect(unconvertibleFigure(wrapper).element.tagName).not.toBe('BUTTON')
  })

  it('opens the five drillable buckets, and names the kind for excluded', async () => {
    const wrapper = mountFooter(FULL)
    await bucketButton(wrapper, 'uncategorised').trigger('click')
    expect(wrapper.emitted('bucket')![0]).toEqual(['uncategorised', undefined])
    await bucketButton(wrapper, 'coverage_limit').trigger('click')
    expect(wrapper.emitted('bucket')![1]).toEqual(['excluded', 'coverage_limit'])
  })

  it('opens unclassified, undated and unaccounted too', async () => {
    const wrapper = mountFooter({
      ...FULL,
      footer: {
        ...FULL.footer,
        undated: { amount_kind: 'undated', amount: '5.00', documents: 1 },
        unaccounted: { amount_kind: 'unaccounted', amount: '1.00', documents: 1 },
      },
    })
    await bucketButton(wrapper, 'unclassified').trigger('click')
    expect(wrapper.emitted('bucket')![0]).toEqual(['unclassified', undefined])
    await bucketButton(wrapper, 'undated').trigger('click')
    expect(wrapper.emitted('bucket')![1]).toEqual(['undated', undefined])
    await bucketButton(wrapper, 'unaccounted').trigger('click')
    expect(wrapper.emitted('bucket')![2]).toEqual(['unaccounted', undefined])
  })

  // An absent field and an empty one are different claims, and only one of
  // them is "nothing was excluded".
  it('renders every block even when its groups are null', () => {
    const wrapper = mountFooter(EMPTY_FOOTER)
    expect(excludedBlock(wrapper).exists()).toBe(true)
    expect(attentionBlock(wrapper).exists()).toBe(true)
    expect(excludedBlock(wrapper).text()).toContain('Nothing excluded')
    expect(attentionBlock(wrapper).text()).toContain('Nothing needs attention')
  })

  it('renders the could-not-be-converted block even when unconvertible is empty', () => {
    const wrapper = mountFooter(EMPTY_FOOTER)
    const block = wrapper.find('[data-testid="spending-footer-unconvertible"]')
    expect(block.exists()).toBe(true)
    expect(block.text()).toContain('Nothing unconverted')
  })

  // Extra coverage beyond the brief's pinned assertions ----------------------

  it('renders the header line as total across payments from documents', () => {
    const wrapper = mountFooter(EMPTY_FOOTER)
    const text = headerBlock(wrapper).text()
    expect(text).toContain('1,250.00')
    expect(text).toContain('14')
    expect(text).toContain('payments')
    expect(text).toContain('16')
    expect(text).toContain('documents')
  })

  it('never adds ChartData.documents to a footer group\'s documents', () => {
    // FULL has data.documents=16 and an excluded group with documents=3 — the
    // rendered footer must show both distinctly, never their sum (19).
    const wrapper = mountFooter(FULL)
    expect(wrapper.text()).not.toContain('19 documents')
  })

  it('pluralises a single refund correctly (no trailing s)', () => {
    const wrapper = mountFooter(WITH_REFUND)
    expect(headerBlock(wrapper).text()).toContain('1 refund netted off')
  })

  it('pluralises multiple refunds', () => {
    const wrapper = mountFooter(chartData({ refund_count: 3, netted_refunds: '90.00' }))
    expect(headerBlock(wrapper).text()).toContain('3 refunds netted off')
  })

  it('renders a currency code beside a convertible amount', () => {
    const wrapper = mountFooter(FULL)
    const row = unconvertibleRow(wrapper)
    expect(row.text()).toContain('JPY')
  })

  // Spec review finding 1 (Critical): an unconvertible amount is denominated
  // in the GROUP's OWN currency (the backend's own docstring), never the
  // chart's display currency — that is the entire reason the row exists. The
  // chart here displays in GBP; the unconvertible group is JPY. The label
  // span alone (`toContain('JPY')`, above) is satisfied whether or not the
  // AMOUNT is correct, since the label always names the currency — this
  // assertion is tied to the amount span itself, and pins the exact grouped
  // digits so reverting to `formatMoney(group.amount, props.data.currency)`
  // (rendering "GBP 3,000.00") turns it red.
  it('formats an unconvertible amount in the GROUP\'s own currency, never the chart\'s display currency', () => {
    const wrapper = mountFooter(FULL)
    const amount = unconvertibleAmount(wrapper)
    expect(amount.text()).toBe('JPY 3,000.00')
    expect(amount.text()).not.toContain('GBP')
  })

  it('sorts multiple real currencies alphabetically before the null entry', () => {
    const rows = unconvertibleRows(mountFooter(WITH_NULL_CURRENCY))
    expect(rows[0]!.text()).toContain('AUD')
    expect(rows[1]!.text()).toContain('CHF')
    expect(rows[2]!.text()).toContain('No currency')
  })

  it('formats every unconvertible row in ITS OWN currency, even with several different ones on screen', () => {
    // Chart currency is GBP (`chartData` default); none of these three
    // groups is in GBP, so a bug that fell back to the chart's currency
    // would render GBP on every row instead of each row's own code.
    const rows = unconvertibleRows(mountFooter(WITH_NULL_CURRENCY))
    const amounts = rows.map((row) => row.get('[data-testid="spending-footer-unconvertible-amount"]').text())
    expect(amounts).toEqual(['AUD 20.00', 'CHF 30.00', '10.00'])
  })

  // The refund count already pluralises correctly; the "documents" figure
  // beside excluded/attention/unconvertible rows must follow the same rule.
  it('pluralises a single document correctly (no trailing s)', () => {
    const wrapper = mountFooter(
      chartData({
        excluded: [{ amount_kind: 'coverage_limit', amount: '10.00', documents: 1 }],
        unclassified: { amount_kind: 'unclassified', amount: '5.00', documents: 1 },
        unconvertible: [{ currency: 'JPY', amount: '100.00', documents: 1 }],
      }),
    )
    expect(bucketButton(wrapper, 'coverage_limit').text()).toContain('1 document')
    expect(bucketButton(wrapper, 'coverage_limit').text()).not.toContain('1 documents')
    expect(bucketButton(wrapper, 'unclassified').text()).toContain('1 document')
    expect(bucketButton(wrapper, 'unclassified').text()).not.toContain('1 documents')
    expect(unconvertibleFigure(wrapper).text()).toContain('1 document')
    expect(unconvertibleFigure(wrapper).text()).not.toContain('1 documents')
  })

  it('pluralises multiple documents', () => {
    const wrapper = mountFooter(
      chartData({
        excluded: [{ amount_kind: 'coverage_limit', amount: '10.00', documents: 4 }],
      }),
    )
    expect(bucketButton(wrapper, 'coverage_limit').text()).toContain('4 documents')
  })

  it('keeps two null-currency entries stable relative to each other', () => {
    const wrapper = mountFooter(
      chartData({
        unconvertible: [
          { currency: null, amount: '10.00', documents: 1 },
          { currency: null, amount: '5.00', documents: 1 },
        ],
      }),
    )
    const rows = unconvertibleRows(wrapper)
    expect(rows).toHaveLength(2)
    expect(rows[0]!.text()).toContain('No currency')
    expect(rows[1]!.text()).toContain('No currency')
  })
})
