import { computed, type ComputedRef, type Ref } from 'vue'
import { useStorage } from '@vueuse/core'

/**
 * Shared time-axis window for the /charts dashboard.
 *
 * Lets the user enforce a consistent x-axis across every chart so they can be
 * compared at a glance — "all", "year to date", "last 12 months", "last 3
 * years". This is a display-only zoom: it sets the Chart.js time-axis min/max
 * and changes nothing about series membership or data. The choice persists
 * per-machine under a single key.
 */
export type Timeframe = 'all' | 'ytd' | '12m' | '3y'

export const CHARTS_TIMEFRAME_STORAGE_KEY = 'library:charts-timeframe'

export interface TimeframeOption {
  value: Timeframe
  label: string
}

export const TIMEFRAME_OPTIONS: TimeframeOption[] = [
  { value: 'all', label: 'All time' },
  { value: 'ytd', label: 'Year to date' },
  { value: '12m', label: 'Last 12 months' },
  { value: '3y', label: 'Last 3 years' },
]

/** Axis bounds as ISO `yyyy-mm-dd` strings, or null for an open end. */
export interface TimeframeBounds {
  min: string | null
  max: string | null
}

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10)
}

/**
 * The `{min, max}` window for a timeframe relative to `now`. "all" is fully
 * open (both null → Chart.js auto-fits the data). Every other timeframe pins
 * `max` to `now` so all charts share the same right edge.
 */
export function timeframeBounds(timeframe: Timeframe, now: Date): TimeframeBounds {
  if (timeframe === 'all') return { min: null, max: null }
  const max = isoDate(now)
  const start = new Date(now)
  if (timeframe === 'ytd') {
    start.setMonth(0, 1)
  } else if (timeframe === '12m') {
    start.setFullYear(start.getFullYear() - 1)
  } else {
    start.setFullYear(start.getFullYear() - 3)
  }
  return { min: isoDate(start), max }
}

export interface ChartsTimeframe {
  timeframe: Ref<Timeframe>
  options: TimeframeOption[]
  bounds: ComputedRef<TimeframeBounds>
}

export function useChartsTimeframe(): ChartsTimeframe {
  const timeframe = useStorage<Timeframe>(CHARTS_TIMEFRAME_STORAGE_KEY, 'all')
  const bounds = computed(() => timeframeBounds(timeframe.value, new Date()))
  return { timeframe, options: TIMEFRAME_OPTIONS, bounds }
}
