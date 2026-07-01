import { computed, type ComputedRef, type Ref } from 'vue'
import { useStorage } from '@vueuse/core'

/**
 * Shared time-axis window for the /charts dashboard and single-chart page.
 *
 * Lets the user enforce a consistent x-axis so charts can be compared at a
 * glance — "all", "year to date", "last quarter", "last 12 months", "last 3
 * years", or a "custom" from/to range. This is a display-only zoom: it sets the
 * Chart.js time-axis min/max and changes nothing about series membership or
 * data. The choice (and any custom dates) persists per-machine.
 *
 * Preset ↔ datepicker interplay: selecting a preset reflects its resolved
 * window into the custom from/to fields (so the datepickers always show the
 * active window), and editing a datepicker flips the selection to "custom".
 */
export type Timeframe = 'all' | 'ytd' | 'lastq' | '12m' | '3y' | 'custom'

export const CHARTS_TIMEFRAME_STORAGE_KEY = 'library:charts-timeframe'
export const CHARTS_CUSTOM_FROM_STORAGE_KEY = 'library:charts-custom-from'
export const CHARTS_CUSTOM_TO_STORAGE_KEY = 'library:charts-custom-to'

export interface TimeframeOption {
  value: Timeframe
  label: string
}

export const TIMEFRAME_OPTIONS: TimeframeOption[] = [
  { value: 'all', label: 'All time' },
  { value: 'ytd', label: 'Year to date' },
  { value: 'lastq', label: 'Last quarter' },
  { value: '12m', label: 'Last 12 months' },
  { value: '3y', label: 'Last 3 years' },
  { value: 'custom', label: 'Custom range' },
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
 * The `{min, max}` window for a *preset* timeframe relative to `now`. "all" is
 * fully open (both null → Chart.js auto-fits the data). "custom" is also open
 * here — its bounds come from the user's from/to fields, resolved in the
 * composable, not from this pure helper. Every bounded preset pins `max` to
 * `now` so all charts share the same right edge.
 */
export function timeframeBounds(timeframe: Timeframe, now: Date): TimeframeBounds {
  if (timeframe === 'all' || timeframe === 'custom') return { min: null, max: null }
  const max = isoDate(now)
  const start = new Date(now)
  if (timeframe === 'ytd') {
    start.setMonth(0, 1)
  } else if (timeframe === 'lastq') {
    start.setMonth(start.getMonth() - 3)
  } else if (timeframe === '12m') {
    start.setFullYear(start.getFullYear() - 1)
  } else {
    start.setFullYear(start.getFullYear() - 3)
  }
  return { min: isoDate(start), max }
}

export interface ChartsTimeframe {
  timeframe: Ref<Timeframe>
  customFrom: Ref<string | null>
  customTo: Ref<string | null>
  options: TimeframeOption[]
  bounds: ComputedRef<TimeframeBounds>
  /** Select a timeframe; reflects a preset's window into the from/to fields. */
  selectTimeframe: (next: Timeframe) => void
  /** Set a custom date; flips the selection to "custom" (user-initiated edit). */
  setCustom: (which: 'from' | 'to', value: string | null) => void
}

export function useChartsTimeframe(): ChartsTimeframe {
  const timeframe = useStorage<Timeframe>(CHARTS_TIMEFRAME_STORAGE_KEY, 'all')
  const customFrom = useStorage<string | null>(CHARTS_CUSTOM_FROM_STORAGE_KEY, null)
  const customTo = useStorage<string | null>(CHARTS_CUSTOM_TO_STORAGE_KEY, null)

  const bounds = computed<TimeframeBounds>(() =>
    timeframe.value === 'custom'
      ? { min: customFrom.value, max: customTo.value }
      : timeframeBounds(timeframe.value, new Date()),
  )

  // Guard: `selectTimeframe` writes the from/to fields programmatically to
  // reflect a preset. That write must NOT be mistaken for a user edit (which
  // flips the selection to "custom"), or picking a preset would immediately
  // bounce back to custom.
  let reflecting = false

  function reflectPreset(next: Timeframe): void {
    const b = timeframeBounds(next, new Date())
    reflecting = true
    customFrom.value = b.min
    customTo.value = b.max
    reflecting = false
  }

  // On load, show the persisted preset's window in the datepickers.
  if (timeframe.value !== 'custom') reflectPreset(timeframe.value)

  function selectTimeframe(next: Timeframe): void {
    if (next !== 'custom') reflectPreset(next)
    timeframe.value = next
  }

  function setCustom(which: 'from' | 'to', value: string | null): void {
    if (which === 'from') customFrom.value = value
    else customTo.value = value
    if (!reflecting) timeframe.value = 'custom'
  }

  return { timeframe, customFrom, customTo, options: TIMEFRAME_OPTIONS, bounds, selectTimeframe, setCustom }
}
