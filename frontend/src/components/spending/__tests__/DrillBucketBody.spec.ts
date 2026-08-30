import { describe, expect, it, vi, beforeEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import DrillBucketBody from '../DrillBucketBody.vue'
import type { ChartArgs, FooterBucket, FooterDocument, FooterDocuments } from '@/api/spending'

const fetchFooterBucket = vi.fn()
vi.mock('@/api/spending', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/spending')>()),
  fetchFooterBucket: (...args: unknown[]) => fetchFooterBucket(...args),
}))

const RouterLinkStub = {
  props: ['to'],
  template: '<a :href="to"><slot /></a>',
}

const ARGS: ChartArgs = { grain: 'month', split: 'category', currency: 'EUR', from: '2026-01-01', to: '2026-08-31' }

function doc(id: number): FooterDocument {
  return {
    id,
    title: `Uncategorised document ${id}`,
    date: '2026-08-04',
    amount: '12.50',
    currency: 'EUR',
    amount_kind: 'coverage_limit',
  }
}

function page(n: number, total = n): FooterDocuments {
  return {
    bucket: 'uncategorised',
    total,
    documents: Array.from({ length: n }, (_, i) => doc(i + 1)),
  }
}

function mountBucketBody(
  overrides: Partial<{ chartId: number; bucket: FooterBucket; amountKind?: string; args: ChartArgs }> = {},
): VueWrapper {
  return mount(DrillBucketBody, {
    props: {
      chartId: 7,
      bucket: 'uncategorised',
      args: ARGS,
      ...overrides,
    },
    global: { stubs: { RouterLink: RouterLinkStub } },
  })
}

async function mountedBucketBody(
  overrides: Partial<{ chartId: number; bucket: FooterBucket; amountKind?: string; args: ChartArgs }> = {},
): Promise<VueWrapper> {
  const wrapper = mountBucketBody(overrides)
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  fetchFooterBucket.mockReset().mockResolvedValue(page(3))
})

describe('DrillBucketBody', () => {
  it('shows the page size against the bucket total, never a silent truncation', async () => {
    fetchFooterBucket.mockResolvedValue({ bucket: 'uncategorised', total: 340, documents: page(100).documents })
    const wrapper = await mountedBucketBody()
    expect(wrapper.text()).toContain('100 of 340')
  })

  it('requires amount_kind for the excluded bucket', async () => {
    await mountedBucketBody({ bucket: 'excluded', amountKind: 'coverage_limit' })
    expect(fetchFooterBucket.mock.calls[0]![2]).toMatchObject({ amount_kind: 'coverage_limit' })
  })

  it('caps its page size at the server maximum', async () => {
    await mountedBucketBody()
    expect(fetchFooterBucket.mock.calls[0]![2]!.limit).toBeLessThanOrEqual(100)
  })

  it('does not send amount_kind for a bucket that has none', async () => {
    await mountedBucketBody({ bucket: 'undated' })
    expect(fetchFooterBucket.mock.calls[0]![2]!.amount_kind).toBeUndefined()
  })

  it('renders every document in the page, each linking to its detail page', async () => {
    const wrapper = await mountedBucketBody()
    const rows = wrapper.findAll('[data-testid="drill-document"]')
    expect(rows).toHaveLength(3)
    expect(wrapper.get('[data-testid="drill-document"] a').attributes('href')).toBe('/documents/1')
  })

  it('shows a real empty state when the bucket is genuinely empty', async () => {
    fetchFooterBucket.mockResolvedValue({ bucket: 'uncategorised', total: 0, documents: [] })
    const wrapper = await mountedBucketBody()
    expect(wrapper.find('[data-testid="drill-empty"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('0 of 0')
  })

  it('shows an error rather than an empty panel when the initial load fails', async () => {
    fetchFooterBucket.mockReset().mockRejectedValue(new Error('boom'))
    const wrapper = await mountedBucketBody()
    expect(wrapper.get('[data-testid="drill-error"]').text()).toBe('Could not load these documents.')
    expect(wrapper.find('[data-testid="drill-empty"]').exists()).toBe(false)
  })

  it('surfaces an error from Show more without discarding the page already shown', async () => {
    fetchFooterBucket.mockResolvedValueOnce({ bucket: 'uncategorised', total: 4, documents: page(3).documents })
    const wrapper = await mountedBucketBody()

    fetchFooterBucket.mockRejectedValueOnce(new Error('boom'))
    await wrapper.get('[data-testid="drill-load-more"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-testid="drill-more-error"]').text()).toBe(
      'Could not load more of these documents.',
    )
    // The rows fetched before the failure are still there.
    expect(wrapper.findAll('[data-testid="drill-document"]')).toHaveLength(3)
  })

  it('fetches the next page on Show more, appending rather than replacing', async () => {
    fetchFooterBucket.mockResolvedValueOnce({ bucket: 'uncategorised', total: 4, documents: page(3).documents })
    const wrapper = await mountedBucketBody()
    expect(wrapper.findAll('[data-testid="drill-document"]')).toHaveLength(3)

    fetchFooterBucket.mockResolvedValueOnce({
      bucket: 'uncategorised',
      total: 4,
      documents: [doc(4)],
    })
    await wrapper.get('[data-testid="drill-load-more"]').trigger('click')
    await flushPromises()

    expect(wrapper.findAll('[data-testid="drill-document"]')).toHaveLength(4)
    expect(fetchFooterBucket.mock.calls[1]![2]).toMatchObject({ offset: 3 })
    // No more to fetch once the page equals the total.
    expect(wrapper.find('[data-testid="drill-load-more"]').exists()).toBe(false)
  })
})
