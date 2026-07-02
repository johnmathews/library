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

// `-v2` bumps the persisted key so the new "by month" default reaches everyone
// once (older machines stored `none` under the bare key); a later manual choice
// persists under this key as usual.
export const CHARTS_GROUPING_STORAGE_KEY = 'library:charts-grouping-v2'

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

/** One contributing document within a bucket, for the tooltip breakdown. */
export interface BucketItem {
  amount: number
  label: string
}

/** A summed bucket: `x` is the ISO period start, `y` the total, `count` the
 *  number of documents that fell in it, `items` the per-document breakdown
 *  (amount + label) that fed the sum, in input order. */
export interface GroupedPoint {
  x: string
  y: number
  count: number
  items: BucketItem[]
}

/** The minimal shape `groupSeriesPoints` needs from a series point. `label` is
 *  optional; it falls back to the date in the per-document breakdown. */
export interface DatedAmount {
  date: string
  amount: string | number
  label?: string
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
    const raw = Number(p.amount)
    const amount = Number.isFinite(raw) ? raw : 0
    const item: BucketItem = { amount, label: p.label ?? p.date }
    const entry = buckets.get(key)
    if (entry) {
      entry.y += amount
      entry.count += 1
      entry.items.push(item)
    } else {
      buckets.set(key, { x: key, y: amount, count: 1, items: [item] })
    }
  }
  return [...buckets.values()].sort((a, b) => a.x.localeCompare(b.x))
}

export interface ChartsGrouping {
  grouping: Ref<ChartGrouping>
  options: GroupingOption[]
}

export function useChartsGrouping(): ChartsGrouping {
  const grouping = useStorage<ChartGrouping>(CHARTS_GROUPING_STORAGE_KEY, 'month')
  return { grouping, options: GROUPING_OPTIONS }
}
