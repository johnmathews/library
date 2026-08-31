import { describe, expect, it, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import PaymentGroup from '../PaymentGroup.vue'

const fetchPayment = vi.fn()
const splitPayment = vi.fn()
vi.mock('@/api/payments', () => ({
  fetchPayment: (...a: unknown[]) => fetchPayment(...a),
  splitPayment: (...a: unknown[]) => splitPayment(...a),
  mergePayment: vi.fn(),
}))

// PaymentGroup rows link to sibling documents via RouterLink; stub it so
// mounting outside a router doesn't throw.
const RouterLinkStub = {
  props: ['to'],
  template: '<a :href="to"><slot /></a>',
}

function mountGroup(documentId = 7) {
  return mount(PaymentGroup, {
    props: { documentId },
    global: { stubs: { RouterLink: RouterLinkStub } },
  })
}

const PAIR = {
  payment_id: 7,
  documents: [
    { id: 7, title: 'Invoice', document_date: '2026-08-04', amount_kind: 'payment_due', reference: null },
    { id: 8, title: 'Receipt', document_date: '2026-08-04', amount_kind: 'payment_made', reference: null },
  ],
}

beforeEach(() => {
  fetchPayment.mockReset()
  splitPayment.mockReset()
})

describe('PaymentGroup', () => {
  it('renders nothing when the document is alone in its payment', async () => {
    fetchPayment.mockResolvedValue({ payment_id: 7, documents: [PAIR.documents[0]] })
    const wrapper = mountGroup()
    await flushPromises()
    expect(wrapper.find('[data-testid="payment-group"]').exists()).toBe(false)
  })

  it('lists both documents when two are collapsed into one payment', async () => {
    fetchPayment.mockResolvedValue(PAIR)
    const wrapper = mountGroup()
    await flushPromises()
    expect(wrapper.findAll('[data-testid="payment-group-row"]')).toHaveLength(2)
  })

  it('splits the pair and re-renders from the response', async () => {
    fetchPayment.mockResolvedValue(PAIR)
    splitPayment.mockResolvedValue({ payment_id: 7, documents: [PAIR.documents[0]] })
    const wrapper = mountGroup()
    await flushPromises()
    await wrapper.get('[data-testid="payment-split"]').trigger('click')
    await flushPromises()
    expect(splitPayment).toHaveBeenCalledWith(7, 8)
    expect(wrapper.find('[data-testid="payment-group"]').exists()).toBe(false)
  })

  it('surfaces a load failure instead of rendering an empty panel', async () => {
    fetchPayment.mockRejectedValue(new Error('nope'))
    const wrapper = mountGroup()
    await flushPromises()
    expect(wrapper.get('[data-testid="payment-error"]').text()).toContain('Could not load')
  })
})
