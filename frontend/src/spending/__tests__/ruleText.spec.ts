import { describe, expect, it } from 'vitest'
import type { FacetRef } from '@/api/facets'
import { ruleSummary, splitSummary } from '../ruleText'

const FACETS: FacetRef[] = [
  {
    key: 'category',
    label: 'Category',
    ordinal: 0,
    values: [
      { key: 'software', label: 'Software', parent_id: null, aliases: [], colour: null },
      { key: 'services', label: 'Services', parent_id: null, aliases: [], colour: null },
    ],
  },
]

describe('ruleSummary', () => {
  it('renders an empty rule as matching every document', () => {
    expect(ruleSummary({ all: [] })).toBe('Every document.')
  })

  it('distinguishes not_in from in', () => {
    const summary = ruleSummary({
      all: [{ facet: 'category', op: 'not_in', values: ['software'] }],
    })
    expect(summary).toContain('is not')
  })

  // This is the case QuestionDraft relies on: it holds no vocabulary, so the
  // default argument has to keep its rendered wording exactly as it was before
  // the function moved out of that component. If this goes red, the default was
  // dropped and QuestionDraft's own tests will have gone red with it.
  it('prints raw keys when no vocabulary is supplied', () => {
    expect(ruleSummary({ all: [{ facet: 'category', op: 'in', values: ['software'] }] })).toBe(
      'category is software',
    )
  })

  it('prints vocabulary labels when one is supplied', () => {
    expect(
      ruleSummary({ all: [{ facet: 'category', op: 'in', values: ['software'] }] }, FACETS),
    ).toBe('Category is Software')
  })

  it('joins several values with "or" and several clauses with "and"', () => {
    const summary = ruleSummary(
      {
        all: [
          { facet: 'category', op: 'in', values: ['software', 'services'] },
          { facet: 'category', op: 'not_in', values: ['services'] },
        ],
      },
      FACETS,
    )
    expect(summary).toBe('Category is Software or Services and Category is not Services')
  })

  // A value deleted from the vocabulary after the chart was saved. The prose
  // must keep naming it: this rule is exactly what the owner opened the editor
  // to repair, and prose that quietly dropped the clause would describe a
  // different rule from the one that is stored.
  it('falls back to the raw key for a value missing from the vocabulary', () => {
    const summary = ruleSummary(
      { all: [{ facet: 'category', op: 'in', values: ['ghost'] }] },
      FACETS,
    )
    expect(summary).toBe('Category is ghost')
  })

  it('falls back to the raw key for a facet missing from the vocabulary', () => {
    const summary = ruleSummary({ all: [{ facet: 'gone', op: 'in', values: ['x'] }] }, FACETS)
    expect(summary).toBe('gone is x')
  })
})

describe('splitSummary', () => {
  it('reports no split axis', () => {
    expect(splitSummary(null)).toBe('Not split.')
  })

  it('prints the raw key without a vocabulary and the label with one', () => {
    expect(splitSummary('category')).toBe('Split by category.')
    expect(splitSummary('category', FACETS)).toBe('Split by Category.')
  })
})
