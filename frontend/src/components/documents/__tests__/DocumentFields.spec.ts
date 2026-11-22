import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import DocumentFields from '../DocumentFields.vue'
import DocumentFieldValue from '../DocumentFieldValue.vue'
import type { DocumentListItem } from '@/api/documents'
import type { DashboardField } from '@/api/settings'

function makeItem(overrides: Partial<DocumentListItem> = {}): DocumentListItem {
  return {
    id: 7,
    title: 'Water bill',
    summary: null,
    kind: { slug: 'invoice', name: 'Invoice' },
    sender: { id: 3, name: 'Vitens' },
    recipient: null,
    tags: [],
    projects: [],
    matters: [],
    document_date: '2019-09-17',
    due_date: '2019-10-01',
    expiry_date: null,
    language: 'nld',
    status: 'indexed',
    mime_type: 'application/pdf',
    page_count: 1,
    created_at: '2026-06-10T12:00:00Z',
    updated_at: '2026-06-11T09:30:00Z',
    has_searchable_pdf: true,
    has_thumbnail: true,
    amount_total: '12.50',
    currency: 'EUR',
    snippet: null,
    rank: null,
    review_status: 'unreviewed',
    review_findings: [],
    ...overrides,
  }
}

function fieldsRow(
  fields: DashboardField[],
  item = makeItem(),
  props: Record<string, unknown> = {},
) {
  return mount(DocumentFields, { props: { item, fields, ...props } })
}

describe('DocumentFields', () => {
  it('renders the chosen fields in the chosen order', () => {
    const text = fieldsRow(['amount', 'sender', 'kind']).text()
    expect(text.indexOf('€12.50')).toBeLessThan(text.indexOf('Vitens'))
    expect(text.indexOf('Vitens')).toBeLessThan(text.indexOf('Invoice'))
  })

  it('leaves out fields that are not chosen', () => {
    const text = fieldsRow(['kind']).text()
    expect(text).toBe('Invoice')
  })

  it('puts Needs review and its reason before every field', () => {
    const item = makeItem({
      review_status: 'needs_review',
      review_findings: [
        { rule: 'amount_currency_coupling', field: 'amount_total', message: 'Amount has no currency' },
      ],
    })
    const w = fieldsRow(['kind'], item)
    const text = w.text()
    expect(w.find('[data-testid="review-badge"]').exists()).toBe(true)
    expect(w.find('[data-testid="review-reason"]').exists()).toBe(true)
    expect(text.indexOf('Needs review')).toBeLessThan(text.indexOf('Invoice'))
  })

  it('leaves the review reason out when asked, keeping the chip', () => {
    const item = makeItem({
      review_status: 'needs_review',
      review_findings: [
        { rule: 'amount_currency_coupling', field: 'amount_total', message: 'Amount has no currency' },
      ],
    })
    const w = fieldsRow(['kind'], item, { reviewReason: false })
    expect(w.find('[data-testid="review-badge"]').exists()).toBe(true)
    expect(w.find('[data-testid="review-reason"]').exists()).toBe(false)
  })

  it('shows the document date with its label, compact on request', () => {
    expect(fieldsRow(['date']).find('[data-testid="doc-date"]').text()).toBe('Date 17 September 2019')
    expect(
      fieldsRow(['date'], makeItem(), { compactDates: true }).find('[data-testid="doc-date"]').text(),
    ).toBe('Date 17 Sep 2019')
    expect(
      fieldsRow(['date'], makeItem(), { dateLabels: 'extra' }).find('[data-testid="doc-date"]').text(),
    ).toBe('17 September 2019')
  })

  it('prefixes the extra dates and slices datetimes to their date', () => {
    const w = fieldsRow(['due_date', 'added_date', 'expiry_date'])
    expect(w.find('[data-testid="doc-due-date"]').text()).toBe('Due 1 October 2019')
    expect(w.find('[data-testid="doc-added-date"]').text()).toBe('Added 10 June 2026')
    // No expiry date on the document: nothing rendered for it.
    expect(w.find('[data-testid="doc-expiry-date"]').exists()).toBe(false)
  })

  it('keeps the extra-date prefix in the list and drops every prefix in the table', () => {
    const list = fieldsRow(['date', 'due_date'], makeItem(), { dateLabels: 'extra' })
    expect(list.find('[data-testid="doc-due-date"]').text()).toBe('Due 1 October 2019')
    const table = fieldsRow(['date', 'due_date'], makeItem(), { dateLabels: 'none' })
    expect(table.find('[data-testid="doc-date"]').text()).toBe('17 September 2019')
    expect(table.find('[data-testid="doc-due-date"]').text()).toBe('1 October 2019')
  })

  it('formats the amount in its currency, and shows the bare figure without one', () => {
    expect(fieldsRow(['amount']).find('.app-doc-card__amount').text()).toBe('€12.50')
    expect(
      fieldsRow(['amount'], makeItem({ currency: null })).find('.app-doc-card__amount').text(),
    ).toBe('12.50')
    expect(
      fieldsRow(['amount'], makeItem({ amount_total: null })).find('.app-doc-card__amount').exists(),
    ).toBe(false)
  })

  it('caps tags at four with +N, as chips or as text', () => {
    const tags = ['a', 'b', 'c', 'd', 'e', 'f'].map((s) => ({ slug: s, name: s.toUpperCase() }))
    const item = makeItem({ tags })
    const chips = fieldsRow(['tags'], item).find('[data-testid="doc-tags"]')
    expect(chips.findAll('span.rounded-full')).toHaveLength(4)
    expect(chips.text()).toContain('+2')
    const text = fieldsRow(['tags'], item, { variant: 'text' }).find('[data-testid="doc-tags"]')
    expect(text.findAll('span.rounded-full')).toHaveLength(0)
    expect(text.text().replace(/\s+/g, ' ')).toBe('A, B, C, D +2')
  })

  it('shows type, language and file type as chips, or as plain text in the text variant', () => {
    const fields: DashboardField[] = ['kind', 'language', 'file_type']
    expect(fieldsRow(fields).findAll('span.rounded-full')).toHaveLength(3)
    const plain = fieldsRow(fields, makeItem(), { variant: 'text' })
    expect(plain.findAll('span.rounded-full')).toHaveLength(0)
    expect(plain.find('.app-doc-card__kind').text()).toBe('Invoice')
    expect(plain.find('.app-doc-card__language').text()).toBe('Dutch')
    expect(plain.find('.app-doc-card__file-type').text()).toBe('PDF')
  })

  it('keeps status as a chip in both variants, and shows nothing for an indexed document', () => {
    for (const variant of ['chips', 'text'] as const) {
      expect(fieldsRow(['status'], makeItem({ status: 'failed' }), { variant }).text()).toBe('Failed')
      expect(fieldsRow(['status'], makeItem({ status: 'ocr' }), { variant }).text()).toBe('Processing')
      expect(fieldsRow(['status'], makeItem(), { variant }).text()).toBe('')
    }
  })

  it('hides an unknown language', () => {
    expect(fieldsRow(['language'], makeItem({ language: 'unknown' })).text()).toBe('')
  })
})

describe('DocumentFieldValue', () => {
  it('renders nothing for a field the document has no value for', () => {
    const w = mount(DocumentFieldValue, {
      props: { item: makeItem({ sender: null }), field: 'sender' },
    })
    expect(w.text()).toBe('')
  })
})
