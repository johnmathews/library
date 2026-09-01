/**
 * Typed API for the `/charts` spending view (docs/spending.md).
 *
 * A chart is a saved question over the document archive — a rule (facet
 * filter), a time grain, and an optional split axis — rendered as a bar per
 * period. `/data` answers the chart at the bar level; `/cell` and the footer
 * routes drill into a single bar or exclusion bucket for the panel beneath it.
 */

import { apiFetch } from './client'

export type Grain = 'week' | 'month' | 'quarter' | 'year'

export interface RuleClause {
  facet: string
  op: 'in' | 'not_in'
  values: string[]
}
export interface Rule {
  all: RuleClause[]
}

export interface Chart {
  id: number
  name: string
  question_text: string
  rule: Rule
  default_grain: Grain
  default_split: string | null
  display_currency: string
  ordinal: number
}

export interface Cell {
  period: string
  split_value: string | null
  total: string
  payments: number
}
export interface SplitValue {
  value: string | null
  label: string
  colour: string | null
}
export interface ExcludedGroup {
  amount_kind: string
  amount: string
  documents: number
}
export interface Unconvertible {
  currency: string | null
  amount: string
  documents: number
}

export interface Footer {
  netted_refunds: string
  refund_count: number
  excluded: ExcludedGroup[]
  unclassified: ExcludedGroup | null
  uncategorised: ExcludedGroup | null
  undated: ExcludedGroup | null
  unaccounted: ExcludedGroup | null
  unconvertible: Unconvertible[]
}

/**
 * `grain`, `split`, `currency`, `since` and `until` echo the RESOLVED
 * arguments. `cellArgs()` sends them back to `/cell` verbatim, which is what
 * makes the panel provably answer the question the bar answered.
 */
export interface ChartData {
  chart_id: number | null
  grain: Grain
  split: string | null
  currency: string
  since: string | null
  until: string | null
  cells: Cell[]
  splits: SplitValue[]
  total: string
  payments: number
  documents: number
  footer: Footer
}

export interface CellDocument {
  id: number
  title: string | null
  date: string | null
  amount: string | null // optional: a MERGE override can pull in an amountless document
  currency: string | null
  amount_kind: string | null
  reference: string | null
  is_canonical: boolean
}
export interface CellPayment {
  payment_id: number
  total: string
  documents: CellDocument[]
}
export interface CellBody {
  period: string
  split_value: string | null
  total: string
  payments: CellPayment[]
  label: string // "" for an unsplit chart
  colour: string | null
}

export interface FooterDocument {
  id: number
  title: string | null
  date: string | null
  amount: string
  currency: string | null
  amount_kind: string | null
}
export interface FooterDocuments {
  bucket: string
  total: number
  documents: FooterDocument[]
}

export interface Draft {
  question: string
  expressible: boolean
  rule: Rule | null
  proposed_split: string | null
  unknown_terms: string[]
  message: string | null
  preview: ChartData | null
}

export const FOOTER_BUCKETS = ['excluded', 'unclassified', 'uncategorised', 'undated', 'unaccounted'] as const
export type FooterBucket = (typeof FOOTER_BUCKETS)[number]

/** The window arguments `/data` and `/cell` must agree on. */
export interface ChartArgs {
  grain?: Grain
  /** `''` clears the split axis; `undefined` takes the chart's default. */
  split?: string | null
  from?: string
  to?: string
  currency?: string
}

/**
 * Window arguments the footer routes accept. `GET
 * /api/spending/{id}/footer/{bucket}` declares no `grain` or `split` query
 * param (it always resolves the chart's default split, §-comment on the
 * handler) — `ChartArgs` is too wide here and sending either is silently
 * ignored, which would trap a caller into thinking it changed the bucket.
 */
export interface FooterArgs {
  from?: string
  to?: string
  currency?: string
}

/**
 * `split` is the trap. The API reads `split=` (empty) as "no split axis" and an
 * ABSENT `split` as "use the chart's default", so a client that drops the key
 * when the user turns the split off silently gets the default back. This
 * builder therefore emits `split` whenever the caller supplied the key at all,
 * including when its value is null or empty.
 */
function windowQuery(args: ChartArgs): Record<string, string | number | undefined> {
  const query: Record<string, string | number | undefined> = {
    grain: args.grain,
    from: args.from,
    to: args.to,
    currency: args.currency,
  }
  if ('split' in args) query.split = args.split ?? ''
  return query
}

/** Echo `/data`'s resolved arguments back, which is what `/cell` requires. */
export function cellArgs(data: ChartData): ChartArgs {
  return {
    grain: data.grain,
    split: data.split,
    currency: data.currency,
    from: data.since ?? undefined,
    to: data.until ?? undefined,
  }
}

/** The server's cap; sending more is a 422. */
export const MAX_LIMIT = 100

/** GET /api/spending — the saved charts, ordinal-ordered. */
export async function listCharts(limit = 100, offset = 0): Promise<Chart[]> {
  const body = await apiFetch<{ charts: Chart[] }>('/api/spending', {
    query: { limit: Math.min(limit, MAX_LIMIT), offset },
  })
  return body.charts
}

export interface ChartIn {
  name: string
  question_text?: string
  rule?: Rule
  default_grain?: Grain
  default_split?: string | null
  display_currency: string
  ordinal?: number
}
export type ChartPatch = Partial<ChartIn>

export interface DraftIn {
  question: string
  display_currency: string
  grain?: Grain
  since?: string
  until?: string
}

/** GET /api/spending/{id} — a single chart's definition. */
export function fetchChart(id: number): Promise<Chart> {
  return apiFetch<Chart>(`/api/spending/${id}`)
}

/** POST /api/spending — save a new chart. */
export function createChart(body: ChartIn): Promise<Chart> {
  return apiFetch<Chart>('/api/spending', { method: 'POST', body })
}

/** PATCH /api/spending/{id} — update fields on an existing chart. */
export function updateChart(id: number, patch: ChartPatch): Promise<Chart> {
  return apiFetch<Chart>(`/api/spending/${id}`, { method: 'PATCH', body: patch })
}

/** DELETE /api/spending/{id} — remove a saved chart. */
export function deleteChart(id: number): Promise<void> {
  return apiFetch<void>(`/api/spending/${id}`, { method: 'DELETE' })
}

/** GET /api/spending/{id}/data — the bar-level series for a chart's window. */
export function fetchChartData(id: number, args: ChartArgs): Promise<ChartData> {
  return apiFetch<ChartData>(`/api/spending/${id}/data`, { query: windowQuery(args) })
}

/**
 * GET /api/spending/{id}/cell — the payments behind a single bar (period ×
 * split value). `splitValue` is OMITTED from the query when null — the API
 * documents an absent `split_value` as the unlabelled bucket, and sending
 * `split_value=` would ask for a bucket whose value is the empty string.
 */
export function fetchCell(
  id: number,
  period: string,
  splitValue: string | null,
  args: ChartArgs,
): Promise<CellBody> {
  const query = { ...windowQuery(args), period } as Record<string, string | number | undefined>
  if (splitValue !== null) query.split_value = splitValue
  return apiFetch<CellBody>(`/api/spending/${id}/cell`, { query })
}

/**
 * GET /api/spending/{id}/footer/{bucket} — the documents behind a footer
 * exclusion bucket. The window portion of the query is built by
 * `windowQuery` itself — the same omit-undefined-key handling `/data` and
 * `/cell` get — rather than a second inline object that would drift from it
 * silently, but `from`/`to`/`currency` are picked out of `opts` BY NAME
 * first and handed to `windowQuery` as a fresh `{ from, to, currency }`
 * literal, never `opts` (or a rest-spread of it) directly. `windowQuery`
 * emits `split` whenever the key is merely PRESENT on the object it is
 * given (see its own docblock — that is what makes an explicitly-cleared
 * split axis distinguishable from an unset one on `/data`), and this route
 * declares no `split` param at all (its own docblock, above) — sending one
 * is silently ignored by the server, which would trap a caller into
 * thinking it changed the bucket. A rest-spread of `opts` would forward
 * whatever extra keys a future caller happened to pass, including a stray
 * `split`; naming the three allowed keys keeps that impossible BY
 * CONSTRUCTION rather than by "no caller does that today".
 */
export function fetchFooterBucket(
  id: number,
  bucket: FooterBucket,
  opts: FooterArgs & { amount_kind?: string; limit?: number; offset?: number } = {},
): Promise<FooterDocuments> {
  const { amount_kind, limit = MAX_LIMIT, offset = 0, from, to, currency } = opts
  return apiFetch<FooterDocuments>(`/api/spending/${id}/footer/${bucket}`, {
    query: {
      ...windowQuery({ from, to, currency }),
      amount_kind,
      limit: Math.min(limit, MAX_LIMIT),
      offset,
    },
  })
}

/** POST /api/spending/draft — turn a free-text question into a rule preview. */
export function draftQuestion(body: DraftIn): Promise<Draft> {
  return apiFetch<Draft>('/api/spending/draft', { method: 'POST', body })
}

/**
 * A rule to answer without saving it — the rule editor's preview.
 *
 * `split` is the trap here too, but for the opposite reason to `windowQuery`'s.
 * There it is a *query* parameter and an absent key means "use the chart's
 * default"; here it is a body field and there is no saved chart to default
 * from, so `null` unambiguously means "no split axis". Callers should still
 * send the key explicitly (`split: draft || null`) rather than omitting it —
 * not because the API would read an absent key differently, but so the request
 * body always states the axis and never leaves a reader guessing which rule
 * applies.
 *
 * `from`/`to` rather than `DraftIn`'s `since`/`until`: the editor hands through
 * the workspace toolbar's own `from`/`to` refs, so a preview answers the window
 * the owner is actually looking at. A preview computed over all time while the
 * chart beneath it shows one quarter would report a different number for the
 * same rule, which is the confusion this whole feature exists to remove.
 */
export interface PreviewIn {
  rule: Rule
  display_currency: string
  grain?: Grain
  split?: string | null
  from?: string
  to?: string
}

/**
 * POST /api/spending/preview — answer a rule that has not been saved.
 *
 * Returns the same `ChartData` shape as `/data`, with `chart_id: null` because
 * there is no chart behind it. Unlike `draftQuestion` this costs no model call:
 * it answers a rule the caller already has rather than deriving one from words.
 */
export function postPreview(body: PreviewIn): Promise<ChartData> {
  return apiFetch<ChartData>('/api/spending/preview', { method: 'POST', body })
}
