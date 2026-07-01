import { describe, it, expect, beforeEach } from 'vitest'
import { nextTick } from 'vue'
import {
  timeframeBounds,
  useChartsTimeframe,
  CHARTS_TIMEFRAME_STORAGE_KEY,
} from '../useChartsTimeframe'

// A fixed reference date so the relative windows are deterministic.
const NOW = new Date('2026-07-01T12:00:00Z')

describe('timeframeBounds', () => {
  it('is fully open for "all"', () => {
    expect(timeframeBounds('all', NOW)).toEqual({ min: null, max: null })
  })

  it('is fully open for "custom" (bounds come from the user fields)', () => {
    expect(timeframeBounds('custom', NOW)).toEqual({ min: null, max: null })
  })

  it('starts at Jan 1 of the current year for "ytd"', () => {
    expect(timeframeBounds('ytd', NOW)).toEqual({ min: '2026-01-01', max: '2026-07-01' })
  })

  it('spans the last 3 months for "lastq"', () => {
    expect(timeframeBounds('lastq', NOW)).toEqual({ min: '2026-04-01', max: '2026-07-01' })
  })

  it('spans the last 12 months for "12m"', () => {
    expect(timeframeBounds('12m', NOW)).toEqual({ min: '2025-07-01', max: '2026-07-01' })
  })

  it('spans the last 3 years for "3y"', () => {
    expect(timeframeBounds('3y', NOW)).toEqual({ min: '2023-07-01', max: '2026-07-01' })
  })

  it('pins max to now for every bounded window (shared right edge)', () => {
    for (const tf of ['ytd', 'lastq', '12m', '3y'] as const) {
      expect(timeframeBounds(tf, NOW).max).toBe('2026-07-01')
    }
  })
})

describe('useChartsTimeframe', () => {
  beforeEach(() => localStorage.clear())

  it('reflects a chosen preset into the custom datepicker fields', () => {
    const { selectTimeframe, customFrom, customTo, timeframe } = useChartsTimeframe()
    selectTimeframe('12m')
    expect(timeframe.value).toBe('12m')
    // The from/to fields now show the preset's resolved window.
    expect(customFrom.value).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    expect(customTo.value).toMatch(/^\d{4}-\d{2}-\d{2}$/)
  })

  it('computes bounds from the user fields when custom is selected', () => {
    const { setCustom, bounds, timeframe } = useChartsTimeframe()
    setCustom('from', '2025-03-01')
    setCustom('to', '2025-09-30')
    // Editing a date flips the selection to custom…
    expect(timeframe.value).toBe('custom')
    // …and the axis window comes straight from those fields.
    expect(bounds.value).toEqual({ min: '2025-03-01', max: '2025-09-30' })
  })

  it('does not flip to custom when a preset reflects its window (no bounce)', () => {
    const { selectTimeframe, timeframe } = useChartsTimeframe()
    selectTimeframe('ytd')
    expect(timeframe.value).toBe('ytd')
  })

  it('persists the selection across composable instances', async () => {
    useChartsTimeframe().selectTimeframe('3y')
    await nextTick() // let useStorage flush to localStorage
    expect(localStorage.getItem(CHARTS_TIMEFRAME_STORAGE_KEY)).toContain('3y')
    // A fresh instance reads the persisted choice.
    expect(useChartsTimeframe().timeframe.value).toBe('3y')
  })
})
