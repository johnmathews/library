/**
 * The chart's x-axis periods, with the empty ones put back.
 *
 * The spending API buckets with `date_trunc(grain, …)` and `GROUP BY`, so a
 * period with no documents produces no cell and would vanish from a category
 * axis: July and September would sit side by side and the gap would read as
 * continuous spending. This fills every period from the first to the last at
 * the chart's grain, so the chart can draw an empty one as a stub (ChartStyle
 * card in the design system).
 */
import type { Grain } from '@/api/spending'

const MONTHS_PER_STEP: Record<Exclude<Grain, 'week'>, number> = { month: 1, quarter: 3, year: 12 }

function parse(period: string): Date {
  const [y, m, d] = period.split('-').map(Number)
  return new Date(Date.UTC(y!, m! - 1, d!))
}

function format(date: Date): string {
  return date.toISOString().slice(0, 10)
}

function next(date: Date, grain: Grain): Date {
  if (grain === 'week') return new Date(date.getTime() + 7 * 86_400_000)
  return new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + MONTHS_PER_STEP[grain], 1))
}

/**
 * Every period from the earliest to the latest in `periods`, one per grain
 * step, sorted. Periods are ISO dates at the grain's start (a week starts on
 * Monday). A period off the grain is kept rather than dropped.
 */
export function fillPeriods(periods: string[], grain: Grain): string[] {
  const seen = new Set(periods)
  const sorted = [...seen].sort()
  if (sorted.length < 2) return sorted
  const last = sorted[sorted.length - 1]!
  for (let d = parse(sorted[0]!); format(d) < last; d = next(d, grain)) seen.add(format(d))
  return [...seen].sort()
}
