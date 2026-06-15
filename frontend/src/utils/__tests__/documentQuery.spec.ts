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
  tags: [],
  language: '',
  status: '',
  dateFrom: '',
  dateTo: '',
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
      tags: [],
      language: 'nld',
      status: 'indexed',
      dateFrom: '2026-05-01',
      dateTo: '2026-05-31',
      page: 2,
    })
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
})

describe('buildDocumentQuery', () => {
  it('round-trips a fully-populated applied state, omitting page 1', () => {
    const applied: AppliedFilters = {
      q: 'rekening',
      kind: 'invoice',
      senderId: '3',
      tags: ['energie', 'wonen'],
      language: 'nld',
      status: 'indexed',
      dateFrom: '2026-05-01',
      dateTo: '2026-05-31',
      page: 1,
    }
    expect(buildDocumentQuery(applied)).toEqual({
      q: 'rekening',
      kind: 'invoice',
      sender_id: '3',
      tag: ['energie', 'wonen'],
      language: 'nld',
      status: 'indexed',
      date_from: '2026-05-01',
      date_to: '2026-05-31',
    })
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
    expect(hasActiveFilters({ ...EMPTY, status: 'failed' })).toBe(true)
  })
})
