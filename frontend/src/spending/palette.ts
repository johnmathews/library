/**
 * Turns a chart's raw splits and cells into the ordered, coloured stack
 * bands every chart component draws (spec §4.12, §9.2).
 *
 * A split axis can carry far more distinct values than a stacked bar can
 * legibly show, and the shared six-slot palette (`@/utils/splitPalette`) has
 * exactly six colours to hand out. This module is the one place that: sums
 * cell totals per split value in exact integer cents; decides which values
 * are big enough to earn their own band and folds the rest into a single
 * "Other" bucket without losing a cent of the total; fixes a deterministic
 * band order so the same chart never repaints on a refetch; and assigns each
 * band a colour — a stored `colour` wins its slot first, everything else is
 * hash-derived and de-collided against what stored colours already claimed.
 * Every consumer (the bar chart, the legend, the drill panel, 4c's
 * vocabulary panel) calls `bands()` and reads the result; none of them
 * derives a colour of its own.
 */

import type { Cell, SplitValue } from '@/api/spending'
import { deriveSlot, SPLIT_PALETTE } from '@/utils/splitPalette'
import { toCents } from './money'

/** Sentinel band value for the folded tail. Never a real split value. */
export const OTHER_VALUE = Symbol.for('spending.other')

/** Fixed colour for the Other band (gray-400) — it never claims a palette slot. */
export const OTHER_COLOUR = { light: '#9ca3af', dark: '#9ca3af' } as const

const MAX_BANDS = SPLIT_PALETTE.length

const NULL_KEY = '\u0000null'
const keyOf = (v: string | null): string => (v === null ? NULL_KEY : v)

// `null` sorts LAST: "no value for this facet" reads as the trailing bucket,
// not as if it happened to come first alphabetically.
const cmpKey = (a: string | null, b: string | null): number =>
  a === null ? (b === null ? 0 : 1) : b === null ? -1 : a < b ? -1 : a > b ? 1 : 0

export interface Band {
  value: string | null | typeof OTHER_VALUE
  label: string
  light: string
  dark: string
  totalCents: number
  members: SplitValue[]
  isOther: boolean
}

interface Ranked {
  value: string | null
  label: string
  colour: string | null
  totalCents: number
}

type Survivor =
  | { isOther: false; value: string | null; label: string; colour: string | null; totalCents: number }
  | {
      isOther: true
      value: typeof OTHER_VALUE
      label: string
      colour: null
      totalCents: number
      members: SplitValue[]
    }

/**
 * Build the ordered, coloured bands for one chart's stack.
 *
 * `splits` is empty for an unsplit chart, in which case there are no bands
 * at all (the chart draws a single unstacked series). Otherwise every entry
 * in `splits` gets a band — even one with no matching cells, which is a real
 * zero, not an absence — until more than six survive the rank, at which
 * point the lowest-ranked tail folds into one `Other` band.
 */
export function bands(splits: SplitValue[], cells: Cell[]): Band[] {
  if (splits.length === 0) return []

  const totals = new Map<string, number>()
  for (const s of splits) totals.set(keyOf(s.value), 0)
  for (const c of cells) {
    const k = keyOf(c.split_value)
    if (!totals.has(k)) continue // a cell for a value not in `splits` isn't a band
    totals.set(k, totals.get(k)! + toCents(c.total))
  }

  // Rank order decides what folds: total descending, ties broken by key
  // ascending so a forced tie (e.g. every band at the same total) still
  // picks a fixed six, not whatever order the caller happened to pass.
  const ranked: Ranked[] = splits.map((s) => ({
    value: s.value,
    label: s.label,
    colour: s.colour,
    totalCents: totals.get(keyOf(s.value))!,
  }))
  ranked.sort((a, b) => b.totalCents - a.totalCents || cmpKey(a.value, b.value))

  const kept = ranked.slice(0, MAX_BANDS)
  const folded = ranked.slice(MAX_BANDS)

  const survivors: Survivor[] = kept.map((r) => ({
    isOther: false,
    value: r.value,
    label: r.label,
    colour: r.colour,
    totalCents: r.totalCents,
  }))

  if (folded.length > 0) {
    survivors.push({
      isOther: true,
      value: OTHER_VALUE,
      label: `Other (${folded.length})`,
      colour: null,
      totalCents: folded.reduce((n, r) => n + r.totalCents, 0),
      members: folded.map((r) => ({ value: r.value, label: r.label, colour: r.colour })),
    })
  }

  // Key order decides the assignment sequence: re-sort the survivors by key
  // ascending (null, then Other, last) so colour assignment below is as
  // deterministic as the fold above.
  survivors.sort((a, b) => {
    if (a.isOther) return b.isOther ? 0 : 1
    if (b.isOther) return -1
    return cmpKey(a.value, b.value)
  })

  const taken = new Set<number>()
  const resolved = new Map<number, { light: string; dark: string }>()

  // Pass 1: a stored colour claims its slot before any derived one is
  // handed out, so an owner's override always wins — but only the FIRST
  // claimant. Two survivors can carry the same stored hex (nothing stops two
  // owners picking identical overrides from the same restricted picker), and
  // if both matched the same slot unconditionally they would render as the
  // same colour — exactly the defect de-collision exists to prevent. So a
  // stored colour that matches a slot ALREADY taken by an earlier survivor is
  // left unresolved here and falls through to pass 2's derived walk, same as
  // if it had never named a colour at all. A stored hex that matches no slot
  // at all is still used verbatim and claims nothing (unchanged).
  survivors.forEach((s, i) => {
    if (s.isOther || s.colour === null) return
    const idx = SPLIT_PALETTE.findIndex((slot) => slot.light === s.colour)
    if (idx === -1) {
      resolved.set(i, { light: s.colour, dark: s.colour })
      return
    }
    if (taken.has(idx)) return // collides with an earlier claimant — pass 2 resolves it
    taken.add(idx)
    resolved.set(i, { light: SPLIT_PALETTE[idx]!.light, dark: SPLIT_PALETTE[idx]!.dark })
  })

  // Pass 2: everything pass 1 didn't resolve — colour-less survivors AND
  // stored-colour survivors that lost a slot collision above — derives a
  // slot from its key and takes it, or walks forward to the next free one.
  // The walk terminates because survivors.length <= SPLIT_PALETTE.length,
  // but it is still bounded rather than trusted to.
  survivors.forEach((s, i) => {
    if (s.isOther || resolved.has(i)) return
    let idx = SPLIT_PALETTE.indexOf(deriveSlot(keyOf(s.value)))
    let steps = 0
    while (taken.has(idx) && steps < SPLIT_PALETTE.length) {
      idx = (idx + 1) % SPLIT_PALETTE.length
      steps++
    }
    taken.add(idx)
    resolved.set(i, { light: SPLIT_PALETTE[idx]!.light, dark: SPLIT_PALETTE[idx]!.dark })
  })

  return survivors.map((s, i) => {
    const colour = s.isOther ? OTHER_COLOUR : resolved.get(i)!
    return {
      value: s.value,
      label: s.label,
      light: colour.light,
      dark: colour.dark,
      totalCents: s.totalCents,
      members: s.isOther ? s.members : [{ value: s.value, label: s.label, colour: s.colour }],
      isOther: s.isOther,
    }
  })
}
