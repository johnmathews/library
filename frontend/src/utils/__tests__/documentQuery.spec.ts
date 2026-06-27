import { describe, expect, it } from 'vitest'
import {
  buildDocumentQuery,
  hasActiveFilters,
  parseDocumentQuery,
  type AppliedFilters,
} from '../documentQuery'

const EMPTY: AppliedFilters = {
  q: '',
  kind: '',
  senderId: '',
  project: '',
  tags: [],
  language: '',
  status: '',
  dateFrom: '',
  dateTo: '',
  review: '',
  page: 1,
}

describe('parseDocumentQuery', () => {
  it('defaults to empty applied state for an empty query', () => {
    expect(parseDocumentQuery({})).toEqual(EMPTY)
  })

  it('parses scalar filters, status and page', () => {
    const applied = parseDocumentQuery({
      q: 'rekening',
      kind: 'invoice',
      sender_id: '3',
      language: 'nld',
      status: 'indexed',
      date_from: '2026-05-01',
      date_to: '2026-05-31',
      page: '2',
    })
    expect(applied).toEqual({
      q: 'rekening',
      kind: 'invoice',
      senderId: '3',
      project: '',
      tags: [],
      language: 'nld',
      status: 'indexed',
      dateFrom: '2026-05-01',
      dateTo: '2026-05-31',
      review: '',
      page: 2,
    })
  })

  it('parses the project URL param into the project field', () => {
    expect(parseDocumentQuery({ project: 'house-purchase' }).project).toBe('house-purchase')
  })

  it('parses review URL param into the review field', () => {
    expect(parseDocumentQuery({ review: 'needs_review' }).review).toBe('needs_review')
  })

  it('parses a single tag into a one-element array (back-compat)', () => {
    expect(parseDocumentQuery({ tag: 'energie' }).tags).toEqual(['energie'])
  })

  it('parses repeated tags into an array', () => {
    expect(parseDocumentQuery({ tag: ['energie', 'wonen'] }).tags).toEqual(['energie', 'wonen'])
  })

  it('clamps a bad page to 1', () => {
    expect(parseDocumentQuery({ page: 'nonsense' }).page).toBe(1)
    expect(parseDocumentQuery({ page: '0' }).page).toBe(1)
  })

  it('ignores null values in repeated tag params', () => {
    expect(parseDocumentQuery({ tag: [null, 'energie'] }).tags).toEqual(['energie'])
  })

  it('treats a bare null query param as empty string', () => {
    expect(parseDocumentQuery({ status: null }).status).toBe('')
  })
})

describe('buildDocumentQuery', () => {
  it('round-trips a fully-populated applied state, omitting page 1', () => {
    const applied: AppliedFilters = {
      q: 'rekening',
      kind: 'invoice',
      senderId: '3',
      project: 'house-purchase',
      tags: ['energie', 'wonen'],
      language: 'nld',
      status: 'indexed',
      dateFrom: '2026-05-01',
      dateTo: '2026-05-31',
      review: '',
      page: 1,
    }
    expect(buildDocumentQuery(applied)).toEqual({
      q: 'rekening',
      kind: 'invoice',
      sender_id: '3',
      project: 'house-purchase',
      tag: ['energie', 'wonen'],
      language: 'nld',
      status: 'indexed',
      date_from: '2026-05-01',
      date_to: '2026-05-31',
    })
  })

  it('round-trips review ⇄ review_status (URL key is "review")', () => {
    const applied = parseDocumentQuery({ review: 'verified' })
    expect(applied.review).toBe('verified')
    expect(buildDocumentQuery(applied)).toEqual({ review: 'verified' })
  })

  it('includes page when greater than 1', () => {
    expect(buildDocumentQuery({ ...EMPTY, q: 'x', page: 3 })).toEqual({ q: 'x', page: '3' })
  })

  it('omits empty filters entirely', () => {
    expect(buildDocumentQuery(EMPTY)).toEqual({})
  })

  it('accepts a page override (used when changing a filter resets to page 1)', () => {
    expect(buildDocumentQuery({ ...EMPTY, q: 'x', page: 5 }, 1)).toEqual({ q: 'x' })
  })
})

describe('hasActiveFilters', () => {
  it('is false for empty state and true once any filter is set', () => {
    expect(hasActiveFilters(EMPTY)).toBe(false)
    expect(hasActiveFilters({ ...EMPTY, q: 'x' })).toBe(true)
    expect(hasActiveFilters({ ...EMPTY, tags: ['energie'] })).toBe(true)
    expect(hasActiveFilters({ ...EMPTY, project: 'house-purchase' })).toBe(true)
    expect(hasActiveFilters({ ...EMPTY, status: 'failed' })).toBe(true)
    expect(hasActiveFilters({ ...EMPTY, page: 5 })).toBe(false)
    expect(hasActiveFilters({ ...EMPTY, review: 'verified' })).toBe(true)
  })
})
