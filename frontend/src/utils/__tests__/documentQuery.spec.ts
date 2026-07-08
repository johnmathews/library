import { describe, expect, it } from 'vitest'
import {
  buildDocumentQuery,
  hasActiveFilters,
  parseDocumentQuery,
  DEFAULT_SORT,
  DEFAULT_SORT_DIRECTION,
  type AppliedFilters,
  type SortField,
  type SortDirection,
} from '../documentQuery'

const EMPTY: AppliedFilters = {
  q: '',
  kind: '',
  senderId: '',
  recipientId: '',
  projects: [],
  tags: [],
  language: '',
  status: '',
  dateFrom: '',
  dateTo: '',
  review: '',
  sort: DEFAULT_SORT,
  dir: DEFAULT_SORT_DIRECTION,
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
      recipientId: '',
      projects: [],
      tags: [],
      language: 'nld',
      status: 'indexed',
      dateFrom: '2026-05-01',
      dateTo: '2026-05-31',
      review: '',
      sort: 'added_date',
      dir: 'desc',
      page: 2,
    })
  })

  it('falls back to the provided sort preference when the URL omits sort/dir', () => {
    const applied = parseDocumentQuery({}, { sort: 'document_date', dir: 'asc' })
    expect(applied.sort).toBe('document_date')
    expect(applied.dir).toBe('asc')
  })

  it('lets an explicit URL sort override the provided preference', () => {
    const applied = parseDocumentQuery(
      { sort: 'added_date', dir: 'desc' },
      { sort: 'document_date', dir: 'asc' },
    )
    expect(applied.sort).toBe('added_date')
    expect(applied.dir).toBe('desc')
  })

  it('ignores a garbage preference and uses the hard default', () => {
    const applied = parseDocumentQuery(
      {},
      { sort: 'bogus' as SortField, dir: 'sideways' as SortDirection },
    )
    expect(applied.sort).toBe(DEFAULT_SORT)
    expect(applied.dir).toBe(DEFAULT_SORT_DIRECTION)
  })

  it('parses the recipient_id URL param into the recipientId field', () => {
    expect(parseDocumentQuery({ recipient_id: '5' }).recipientId).toBe('5')
  })

  it('parses repeated project URL params into the projects field', () => {
    expect(parseDocumentQuery({ project: 'house-purchase' }).projects).toEqual(['house-purchase'])
    expect(parseDocumentQuery({ project: ['house-purchase', 'taxes'] }).projects).toEqual([
      'house-purchase',
      'taxes',
    ])
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
      recipientId: '5',
      projects: ['house-purchase', 'taxes'],
      tags: ['energie', 'wonen'],
      language: 'nld',
      status: 'indexed',
      dateFrom: '2026-05-01',
      dateTo: '2026-05-31',
      review: '',
      sort: 'added_date',
      dir: 'desc',
      page: 1,
    }
    expect(buildDocumentQuery(applied)).toEqual({
      q: 'rekening',
      kind: 'invoice',
      sender_id: '3',
      recipient_id: '5',
      project: ['house-purchase', 'taxes'],
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
    expect(hasActiveFilters({ ...EMPTY, recipientId: '5' })).toBe(true)
    expect(hasActiveFilters({ ...EMPTY, projects: ['house-purchase'] })).toBe(true)
    expect(hasActiveFilters({ ...EMPTY, status: 'failed' })).toBe(true)
    expect(hasActiveFilters({ ...EMPTY, page: 5 })).toBe(false)
    expect(hasActiveFilters({ ...EMPTY, review: 'verified' })).toBe(true)
  })

  it('does NOT treat a non-default sort/direction as an active filter', () => {
    expect(hasActiveFilters({ ...EMPTY, sort: 'document_date' })).toBe(false)
    expect(hasActiveFilters({ ...EMPTY, dir: 'asc' })).toBe(false)
  })
})

describe('sort round-trip', () => {
  // The default is added_date/desc, so those are the values omitted from the URL.
  it('omits sort/direction at their defaults', () => {
    expect(buildDocumentQuery(EMPTY)).toEqual({})
    expect(buildDocumentQuery({ ...EMPTY, sort: 'added_date', dir: 'desc' })).toEqual({})
  })

  it('emits sort only when the field is non-default', () => {
    expect(buildDocumentQuery({ ...EMPTY, sort: 'document_date' })).toEqual({
      sort: 'document_date',
    })
  })

  it('emits direction only when non-default (field may stay default)', () => {
    expect(buildDocumentQuery({ ...EMPTY, dir: 'asc' })).toEqual({ dir: 'asc' })
    expect(buildDocumentQuery({ ...EMPTY, sort: 'document_date', dir: 'asc' })).toEqual({
      sort: 'document_date',
      dir: 'asc',
    })
  })

  it('parse⇄build is symmetric for a non-default sort', () => {
    const applied = parseDocumentQuery({ sort: 'document_date', dir: 'asc' })
    expect(applied.sort).toBe('document_date')
    expect(applied.dir).toBe('asc')
    expect(buildDocumentQuery(applied)).toEqual({ sort: 'document_date', dir: 'asc' })
  })

  it('falls back to defaults for unknown sort/direction values', () => {
    const applied = parseDocumentQuery({ sort: 'bogus', dir: 'sideways' })
    expect(applied.sort).toBe('added_date')
    expect(applied.dir).toBe('desc')
  })
})
