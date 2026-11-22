import { describe, expect, it } from 'vitest'

import { fillPeriods } from '../periods'

describe('fillPeriods', () => {
  it('adds the months with no documents between the first and the last', () => {
    expect(fillPeriods(['2026-07-01', '2026-09-01'], 'month')).toEqual(['2026-07-01', '2026-08-01', '2026-09-01'])
  })

  it('crosses a year boundary by month', () => {
    expect(fillPeriods(['2025-11-01', '2026-02-01'], 'month')).toEqual([
      '2025-11-01',
      '2025-12-01',
      '2026-01-01',
      '2026-02-01',
    ])
  })

  it('steps weeks from Monday to Monday, across a leap February', () => {
    // 2028-02-21 and 2028-03-06 are Mondays; 2028 is a leap year.
    expect(fillPeriods(['2028-02-21', '2028-03-06'], 'week')).toEqual(['2028-02-21', '2028-02-28', '2028-03-06'])
  })

  it('steps quarters and years', () => {
    expect(fillPeriods(['2025-10-01', '2026-04-01'], 'quarter')).toEqual(['2025-10-01', '2026-01-01', '2026-04-01'])
    expect(fillPeriods(['2023-01-01', '2026-01-01'], 'year')).toEqual([
      '2023-01-01',
      '2024-01-01',
      '2025-01-01',
      '2026-01-01',
    ])
  })

  it('sorts its input and drops duplicates', () => {
    expect(fillPeriods(['2026-09-01', '2026-07-01', '2026-09-01'], 'month')).toEqual([
      '2026-07-01',
      '2026-08-01',
      '2026-09-01',
    ])
  })

  it('returns a single period or none unchanged', () => {
    expect(fillPeriods(['2026-07-01'], 'month')).toEqual(['2026-07-01'])
    expect(fillPeriods([], 'month')).toEqual([])
  })

  it('keeps a period that is off the grain rather than losing it', () => {
    expect(fillPeriods(['2026-07-01', '2026-07-15', '2026-08-01'], 'month')).toEqual([
      '2026-07-01',
      '2026-07-15',
      '2026-08-01',
    ])
  })
})
