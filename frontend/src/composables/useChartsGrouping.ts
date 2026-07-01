import type { Ref } from 'vue'
import { useStorage } from '@vueuse/core'
import { parseISO, startOfWeek, startOfMonth, startOfQuarter, startOfYear } from 'date-fns'

/**
 * Time-bucket grouping for chart series.
 *
 * By default each bar is a single document. Grouping instead buckets the
 * documents by calendar period (week / month / quarter / year) and SUMS the
 * amounts in each bucket into one bar — the natural view for spending or
 * invoice series where several documents can land in the same period. Like the
 * timeframe window this is a display-only transform of the same points; it
 * changes nothing about membership. The choice persists per-machine.
 */
export type ChartGrouping = 'none' | 'week' | 'month' | 'quarter' | 'year'

export const CHARTS_GROUPING_STORAGE_KEY = 'library:charts-grouping'

export interface GroupingOption {
  value: ChartGrouping
  label: string
}

export const GROUPING_OPTIONS: GroupingOption[] = [
  { value: 'none', label: 'No grouping' },
  { value: 'week', label: 'By week' },
  { value: 'month', label: 'By month' },
  { value: 'quarter', label: 'By quarter' },
  { value: 'year', label: 'By year' },
]

/** A summed bucket: `x` is the ISO period start, `y` the total, `count` the
 *  number of documents that fell in it. */
export interface GroupedPoint {
  x: string
  y: number
  count: number
}

/** The minimal shape `groupSeriesPoints` needs from a series point. */
export interface DatedAmount {
  date: string
  amount: string | number
}

function bucketStart(date: Date, grouping: Exclude<ChartGrouping, 'none'>): Date {
  switch (grouping) {
    case 'week':
      // ISO-style week starting Monday.
      return startOfWeek(date, { weekStartsOn: 1 })
    case 'month':
      return startOfMonth(date)
    case 'quarter':
      return startOfQuarter(date)
    case 'year':
      return startOfYear(date)
  }
}

function isoDate(d: Date): string {
  // Local-date ISO (yyyy-mm-dd); the parts come from a date-only string so
  // there is no timezone ambiguity to worry about.
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

/**
 * Bucket `points` by `grouping` and sum the amounts in each bucket. Returns
 * buckets sorted ascending by period start. Non-numeric amounts contribute 0.
 * Callers must not pass `'none'` — ungrouped rendering keeps the per-document
 * points as-is.
 */
export function groupSeriesPoints(
  points: readonly DatedAmount[],
  grouping: Exclude<ChartGrouping, 'none'>,
): GroupedPoint[] {
  const buckets = new Map<string, GroupedPoint>()
  for (const p of points) {
    const start = bucketStart(parseISO(p.date), grouping)
    const key = isoDate(start)
    const amount = Number(p.amount)
    const entry = buckets.get(key)
    if (entry) {
      entry.y += Number.isFinite(amount) ? amount : 0
      entry.count += 1
    } else {
      buckets.set(key, { x: key, y: Number.isFinite(amount) ? amount : 0, count: 1 })
    }
  }
  return [...buckets.values()].sort((a, b) => a.x.localeCompare(b.x))
}

export interface ChartsGrouping {
  grouping: Ref<ChartGrouping>
  options: GroupingOption[]
}

export function useChartsGrouping(): ChartsGrouping {
  const grouping = useStorage<ChartGrouping>(CHARTS_GROUPING_STORAGE_KEY, 'none')
  return { grouping, options: GROUPING_OPTIONS }
}
