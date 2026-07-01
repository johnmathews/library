import { describe, it, expect } from 'vitest'
import { timeframeBounds } from '../useChartsTimeframe'

// A fixed reference date so the relative windows are deterministic.
const NOW = new Date('2026-07-01T12:00:00Z')

describe('timeframeBounds', () => {
  it('is fully open for "all"', () => {
    expect(timeframeBounds('all', NOW)).toEqual({ min: null, max: null })
  })

  it('starts at Jan 1 of the current year for "ytd"', () => {
    expect(timeframeBounds('ytd', NOW)).toEqual({ min: '2026-01-01', max: '2026-07-01' })
  })

  it('spans the last 12 months for "12m"', () => {
    expect(timeframeBounds('12m', NOW)).toEqual({ min: '2025-07-01', max: '2026-07-01' })
  })

  it('spans the last 3 years for "3y"', () => {
    expect(timeframeBounds('3y', NOW)).toEqual({ min: '2023-07-01', max: '2026-07-01' })
  })

  it('pins max to now for every bounded window (shared right edge)', () => {
    for (const tf of ['ytd', '12m', '3y'] as const) {
      expect(timeframeBounds(tf, NOW).max).toBe('2026-07-01')
    }
  })
})
