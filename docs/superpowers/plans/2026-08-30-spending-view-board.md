# Spending View Board Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the `/charts` spending view — board, workspace, chart, footer, drill panel, draft flow and empty state — against the frozen, deployed 4a API, and retire the old board without reddening e2e.

**Architecture:** A new `frontend/src/spending/` module holds the two pure pieces (exact money arithmetic, and the fold-and-colour assignment) so no component owns colour or money policy; `frontend/src/api/spending.ts` is plain typed functions over `apiFetch` in the shape of `payments.ts` and `facets.ts`, with no store — the board holds its own state and there is no cross-view state to justify one. Nine components under `components/spending/` and two views. The drill panel is **one shell with three bodies**, so the responsive behaviour exists once.

**Tech Stack:** Vue 3.5 `<script setup>`, TypeScript, Vue Router 5, Tailwind 4 (`@theme` tokens in `main.css`, component classes in `utility-patterns.css`), Chart.js 4 + vue-chartjs 5, sortablejs, Vitest + `@vue/test-utils`, Playwright.

**Spec:** [docs/superpowers/specs/2026-08-30-charts-view-design.md](../specs/2026-08-30-charts-view-design.md) — plan 4b is its §4. Read §2 (six decisions already taken), §4.4, and **§4.12 and §4.13**, which carry the computed palette and the two settled interaction shapes this plan does not re-argue. Also [docs/charts.md](../../charts.md) §11 (the API) and §13 (known limits).

## Global Constraints

- **This repository is PUBLIC.** No real sender name, personal name, address, vehicle registration or real monetary amount reaches code, tests, fixtures, docs, journal entries, commit messages or PR bodies. Invent everything. GitGuardian does not catch this class — grep before committing.
- **`limit <= 100` on every list call**, and **asserted in a unit test**. A mocked `fetch` does not enforce the server's cap, so the assertion is the only thing that does. `GET /api/spending` and `GET /api/spending/{id}/footer/{bucket}` both 422 above 100.
- **Money travels as a decimal string** (`"1284.50"`), never a JSON number — verified against `tests/test_api_spending.py`, which asserts `body["total"] == "60.00"`. Type every money field `string` and do arithmetic in integer cents (Task 1). A float subtraction of two 2dp decimals prints `142.29999999999998`.
- **`make lint` does NOT run eslint or vue-tsc.** Before pushing, run all four from `frontend/`: `npm run lint`, `npm run type-check`, `npm run test:unit`, `npm run test:e2e`.
- **A `cd frontend &&` in one Bash call moves every later call.** Use absolute paths or re-`cd`.
- **Assert DOM outcomes, never class names.** Tailwind's utilities layer beats `utility-patterns.css` regardless of specificity, so a class assertion can pass while the rendered result is wrong. The filter-bar specs assert on `data-testid` for exactly this reason (`docs/frontend-view-principles.md` §5.1).
- **Never `isVisible()` on a `v-show` element**, and no `except -> skip`-shaped guards. A skip reports as success.
- **Forcing dark mode in a unit test means seeding `localStorage['vueuse-color-scheme'] = 'dark'` before mount.** `useDark` *writes* the `<html>` class; it does not read it at mount, so toggling the class directly does nothing and the test silently stays in light mode — passing while asserting nothing. (Found on Task 4.)
- **Any component that reads `useDark` must assert the dark branch too.** A `isDark.value ? band.dark : band.light` ternary whose dark arm no test ever takes leaves half the palette — all six dark hexes — unexercised, in a feature whose entire promise is being correctly coloured in both themes. Mount once per theme and assert the rendered colour differs. (Found on Task 3, which shipped with the dark arm uncovered; the gap is cheap to close per component and expensive to find later.)
- **The frontend coverage gate is enforced and can red CI**: `vitest.config.ts` sets lines/statements/functions 85% and branches 75%, and `vitest run --coverage` exits non-zero below them. `SpendingChart.vue` currently sits **exactly** on the 75% branch line with no margin, so a later edit adding an unexercised branch to it reds the build. Cover new branches as you add them rather than relying on the aggregate to absorb them.
- **Every unit test gets a mutation check**: break the implementation, confirm the test goes red, restore it. Several suites in this repository have passed with the feature under test entirely disabled.
- **E2E assertions must hold on all three viewport projects** — chromium 1280, mobile-webkit 375, tablet-webkit 656 — or be explicitly `test.skip`ped for a named geometric reason, as `charts-layout.spec.ts` already does.
- **E2E fixtures must not carry a `document_date`.** One does, and it reorders the dashboard, breaking every spec that clicks the first tile.
- **Direct pushes to `main` are rejected by a ruleset.** Everything goes through a PR. Before `make deploy`, confirm the **`promote`** job on `main` concluded `success` — and note `promote` is not scheduled until `build` finishes, so "all jobs complete" can be true while `promote` does not yet exist.

### The API, as deployed

Every path below is live at `main@60c95d`/alembic `0037`. Query parameter names are **not** the field names: `from` and `to` are the aliases for `since` and `until`.

```
GET    /api/spending?limit&offset                 -> {charts: ChartOut[]}   limit<=100, default 25
POST   /api/spending                              -> ChartOut (201)
GET    /api/spending/{id}                         -> ChartOut
PATCH  /api/spending/{id}                         -> ChartOut
DELETE /api/spending/{id}                         -> 204
POST   /api/spending/draft                        -> DraftOut
GET    /api/spending/{id}/data?grain&split&from&to&currency          -> DataOut
GET    /api/spending/{id}/cell?period&split_value&<all of /data's>   -> CellOutBody
GET    /api/spending/{id}/footer/{bucket}?amount_kind&from&to&currency&limit&offset
                                                  -> FooterDocumentsOut
GET    /api/facets/counts                         -> per-value documents, first_date, last_date
```

`Grain` is `week | month | quarter | year`. `bucket` is one of `excluded`, `unclassified`, `uncategorised`, `undated`, `unaccounted`; `excluded` **requires** `amount_kind`. `unconvertible` and `refund_count` are **not** buckets and 422 if sent.

### Five contracts that bite

Each of these is a documented behaviour of the deployed API, not a style preference.

1. **`/cell` gets `/data`'s echoed arguments verbatim.** `DataOut` echoes the *resolved* `grain`, `split`, `currency`, `since`, `until` for this purpose. Send them back unmodified plus the cell's own `period`. An off-boundary `period` is a 422 whose `detail` names the correct boundary — render that, never an empty panel.
2. **`split=` (empty string) clears the split; an omitted `split` takes the chart's default.** A client that drops the key when the user turns the split off gets the default back. One query builder owns this and always sends the key.
3. **Never sum `documents[].amount`** to reconstruct a bar: a merged pair doubles it, a group member outside the period is still listed, and an unconvertible member is listed but not counted. `CellPaymentOut.total` is the only number that matches, and `payments[]` are apportioned to sum exactly to `CellOutBody.total`, so summing *those* is safe.
4. **`CellOutBody.label` is `""` for an unsplit chart** — not a placeholder, and distinct from an empty label on a real bucket, which cannot occur. Title the panel from the chart's name in that case.
5. **`FooterDocumentsOut.total` is the bucket's size before paging.** Render `"100 of 340"`, never a silent truncation.

---

## File Structure

| file | responsibility |
| --- | --- |
| `frontend/src/spending/money.ts` | **create** — decimal-string ↔ integer cents, and display formatting |
| `frontend/src/utils/splitPalette.ts` | **create** — the six-slot palette shared with plan 4c (4c owns it; see Task 2) |
| `frontend/src/spending/palette.ts` | **create** — the fold and per-chart slot assignment |
| `frontend/src/api/spending.ts` | **create** — typed functions over `apiFetch` for all of the above routes |
| `frontend/src/components/spending/SpendingChart.vue` | **create** — the stacked-bar mark |
| `frontend/src/components/spending/SpendingLegend.vue` | **create** — swatch/label/value, isolate and exclude |
| `frontend/src/components/spending/SpendingFooter.vue` | **create** — §4.5's eight fields in three blocks |
| `frontend/src/components/spending/SpendingDrillPanel.vue` | **create** — the shell: dialog, container query, focus, close |
| `frontend/src/components/spending/DrillCellBody.vue` | **create** — payments → documents, with `FacetEditor` and `PaymentGroup` |
| `frontend/src/components/spending/DrillBucketBody.vue` | **create** — a footer bucket's documents, paged |
| `frontend/src/components/spending/DrillOtherBody.vue` | **create** — the folded values for one period |
| `frontend/src/components/spending/SpendingCard.vue` | **create** — one board card |
| `frontend/src/components/spending/QuestionDraft.vue` | **create** — the three-state draft flow |
| `frontend/src/components/spending/SpendingEmptyState.vue` | **create** — "All spending" plus facet-count proposals |
| `frontend/src/views/SpendingBoardView.vue` | **create** — `/charts` |
| `frontend/src/views/SpendingWorkspaceView.vue` | **create** — `/charts/:chartId(\d+)` |
| `frontend/src/router/index.ts` | modify — swap `/charts`, add the digit-constrained workspace **before** `/charts/:seriesId` |
| `frontend/src/views/ChartsView.vue` | **delete** |
| `frontend/src/views/__tests__/ChartsView.spec.ts` | **delete** |
| `frontend/e2e/charts.spec.ts` | **delete**, replaced by `spending-board.spec.ts` |
| `frontend/e2e/charts-layout.spec.ts` | **delete**, replaced by `spending-layout.spec.ts` |
| `frontend/e2e/spending-board.spec.ts` | **create** — empty state → chart → workspace → drill → delete |
| `frontend/e2e/spending-layout.spec.ts` | **create** — the measured container-query guards |
| `frontend/e2e/smart-groups.spec.ts` | modify — repoint its `/charts`-through-the-sidebar entry |
| `docs/frontend.md` | modify — the new views, plus the stamp |
| `journal/260830-spending-view-board.md` | **create** |

Unit specs live beside their subject in `frontend/src/**/__tests__/`.

---

### Task 1: Money and the API client

**Files:**
- Create: `frontend/src/spending/money.ts`, `frontend/src/spending/__tests__/money.spec.ts`
- Create: `frontend/src/api/spending.ts`, `frontend/src/api/__tests__/spending.spec.ts`

**Interfaces:**
- Consumes: `apiFetch`, `ApiError` from `@/api/client`.
- Produces: `toCents(amount: string): number`, `fromCents(cents: number): string`, `formatMoney(amount: string, currency: string): string`; and the client functions plus every response interface listed below. Tasks 2–13 import from these two modules and nowhere else.

- [ ] **Step 1: Write the failing money spec**

`frontend/src/spending/__tests__/money.spec.ts`. These assertions are the requirement; they were run against the reference implementation before this plan was written and all pass.

```ts
import { describe, expect, it } from 'vitest'
import { fromCents, toCents } from '../money'

describe('money', () => {
  it('parses the decimal strings the API actually sends', () => {
    expect(toCents('1284.50')).toBe(128450)
    expect(toCents('-49.00')).toBe(-4900)
    expect(toCents('0')).toBe(0)
    expect(toCents('7.5')).toBe(750)
  })

  it('rejects anything that is not a decimal amount', () => {
    expect(() => toCents('1,284.50')).toThrow()
    expect(() => toCents('')).toThrow()
  })

  // The reason this module exists: a float subtraction of two 2dp decimals
  // prints 142.29999999999998, and that number would reach a headline.
  it('stays exact where floats do not', () => {
    expect(fromCents(toCents('0.10') + toCents('0.20'))).toBe('0.30')
    expect(fromCents(toCents('1284.50') - toCents('1142.20'))).toBe('142.30')
  })

  it('round-trips negatives and sub-unit values', () => {
    expect(fromCents(-4900)).toBe('-49.00')
    expect(fromCents(-5)).toBe('-0.05')
    expect(fromCents(0)).toBe('0.00')
  })
})
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd /Users/john/projects/syncthing/agent-lxc/library-4b/frontend && npx vitest run src/spending/__tests__/money.spec.ts`
Expected: FAIL — cannot resolve `../money`.

- [ ] **Step 3: Write `money.ts`**

This implementation was executed against the assertions above before being written here.

```ts
/**
 * Exact arithmetic over the decimal strings the spending API sends.
 *
 * Money crosses the wire as a string (`"1284.50"`), never a JSON number, and
 * the two places this view does arithmetic — ranking split values for the fold,
 * and the headline's period-over-period delta — are both places where a float
 * result would be rendered. `1284.50 - 1142.20` is `142.29999999999998` in
 * IEEE754, so everything is done in integer cents and formatted back.
 */

/** Parse a decimal amount string to integer cents. Throws on anything else. */
export function toCents(amount: string): number {
  const match = /^(-?)(\d+)(?:\.(\d{1,2}))?$/.exec(amount.trim())
  if (!match) throw new Error(`not a decimal amount: ${JSON.stringify(amount)}`)
  const [, sign, whole, frac = ''] = match
  const cents = Number(whole) * 100 + Number((frac + '00').slice(0, 2))
  return sign === '-' ? -cents : cents
}

/** Render integer cents back as a 2dp decimal string. */
export function fromCents(cents: number): string {
  const negative = cents < 0
  const abs = Math.abs(cents)
  return `${negative ? '-' : ''}${Math.floor(abs / 100)}.${String(abs % 100).padStart(2, '0')}`
}

/**
 * A money amount with its currency, grouped for reading: `EUR 1,284.50`.
 * Currency goes in front as a plain code rather than a symbol — the display
 * currency is a chart-level choice the toolbar names, and a symbol would imply
 * the underlying documents were in it.
 */
export function formatMoney(amount: string, currency: string): string {
  const cents = toCents(amount)
  const negative = cents < 0
  const abs = Math.abs(cents)
  const whole = String(Math.floor(abs / 100)).replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return `${negative ? '-' : ''}${currency} ${whole}.${String(abs % 100).padStart(2, '0')}`
}
```

- [ ] **Step 4: Run it and watch it pass**

Run: `cd /Users/john/projects/syncthing/agent-lxc/library-4b/frontend && npx vitest run src/spending/__tests__/money.spec.ts`
Expected: PASS.

- [ ] **Step 5: Mutation-check it**

Change `toCents` to `return Number(amount) * 100`. Run the spec. Expected: the exactness test goes RED (`0.30000000000000004`-shaped failure). Restore.

- [ ] **Step 6: Write `spending.ts`**

Plain typed functions over `apiFetch`, in the shape of `payments.ts`. Type every money field `string`. The full interface set:

```ts
export type Grain = 'week' | 'month' | 'quarter' | 'year'

export interface RuleClause { facet: string; op: 'in' | 'not_in'; values: string[] }
export interface Rule { all: RuleClause[] }

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

export interface Cell { period: string; split_value: string | null; total: string; payments: number }
export interface SplitValue { value: string | null; label: string; colour: string | null }
export interface ExcludedGroup { amount_kind: string; amount: string; documents: number }
export interface Unconvertible { currency: string | null; amount: string; documents: number }

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
  amount: string | null      // optional: a MERGE override can pull in an amountless document
  currency: string | null
  amount_kind: string | null
  reference: string | null
  is_canonical: boolean
}
export interface CellPayment { payment_id: number; total: string; documents: CellDocument[] }
export interface CellBody {
  period: string
  split_value: string | null
  total: string
  payments: CellPayment[]
  label: string              // "" for an unsplit chart
  colour: string | null
}

export interface FooterDocument {
  id: number; title: string | null; date: string | null
  amount: string; currency: string | null; amount_kind: string | null
}
export interface FooterDocuments { bucket: string; total: number; documents: FooterDocument[] }

export interface Draft {
  question: string
  expressible: boolean
  rule: Rule | null
  proposed_split: string | null
  unknown_terms: string[]
  message: string | null
  preview: ChartData | null
}

export interface FacetCount {
  facet_key: string; value_key: string
  documents: number; first_date: string | null; last_date: string | null
}

export const FOOTER_BUCKETS = [
  'excluded', 'unclassified', 'uncategorised', 'undated', 'unaccounted',
] as const
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
```

The one piece of real logic in this file — the query builder both `/data` and `/cell` go through:

```ts
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
```

Then the functions. `listCharts` caps `limit`, and so does `fetchFooterBucket`:

```ts
/** The server's cap; sending more is a 422. */
export const MAX_LIMIT = 100

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

export function fetchChart(id: number): Promise<Chart> {
  return apiFetch<Chart>(`/api/spending/${id}`)
}

export function createChart(body: ChartIn): Promise<Chart> {
  return apiFetch<Chart>('/api/spending', { method: 'POST', body })
}

export function updateChart(id: number, patch: ChartPatch): Promise<Chart> {
  return apiFetch<Chart>(`/api/spending/${id}`, { method: 'PATCH', body: patch })
}

export function deleteChart(id: number): Promise<void> {
  return apiFetch<void>(`/api/spending/${id}`, { method: 'DELETE' })
}

export function fetchChartData(id: number, args: ChartArgs): Promise<ChartData> {
  return apiFetch<ChartData>(`/api/spending/${id}/data`, { query: windowQuery(args) })
}

/**
 * `splitValue` is OMITTED from the query when null — the API documents an
 * absent `split_value` as the unlabelled bucket, and sending `split_value=`
 * would ask for a bucket whose value is the empty string.
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

export function fetchFooterBucket(
  id: number,
  bucket: FooterBucket,
  opts: ChartArgs & { amount_kind?: string; limit?: number; offset?: number } = {},
): Promise<FooterDocuments> {
  const { amount_kind, limit = MAX_LIMIT, offset = 0, ...args } = opts
  return apiFetch<FooterDocuments>(`/api/spending/${id}/footer/${bucket}`, {
    query: {
      ...windowQuery(args),
      amount_kind,
      limit: Math.min(limit, MAX_LIMIT),
      offset,
    },
  })
}

export function draftQuestion(body: DraftIn): Promise<Draft> {
  return apiFetch<Draft>('/api/spending/draft', { method: 'POST', body })
}

export async function fetchFacetCounts(): Promise<FacetCount[]> {
  const body = await apiFetch<{ counts: FacetCount[] }>('/api/facets/counts')
  return body.counts
}
```

The `{counts: [...]}` envelope is read from `FacetCountsOut` in
`src/library/api/facets.py`, not guessed — the house style is an envelope
(`{facets: []}`, `{charts: []}`) and this route follows it.

- [ ] **Step 7: Write the client spec**

`frontend/src/api/__tests__/spending.spec.ts`, in the `savedViews.spec.ts` idiom (`vi.stubGlobal('fetch', …)`, assert on the URL and init). It must cover, at minimum:

```ts
it('caps limit at the server maximum', async () => {
  respondWith({ charts: [] })
  await listCharts(500)
  expect(String(fetchMock.mock.calls[0]![0])).toContain('limit=100')
})

it('caps the footer bucket limit too', async () => {
  respondWith({ bucket: 'uncategorised', total: 0, documents: [] })
  await fetchFooterBucket(1, 'uncategorised', { limit: 500 })
  expect(String(fetchMock.mock.calls[0]![0])).toContain('limit=100')
})

// The split trap, both directions.
it('sends split= when the split is cleared', async () => {
  respondWith(DATA)
  await fetchChartData(1, { split: null })
  expect(String(fetchMock.mock.calls[0]![0])).toMatch(/[?&]split=(&|$)/)
})

it('omits split entirely when the caller did not supply it', async () => {
  respondWith(DATA)
  await fetchChartData(1, { grain: 'month' })
  expect(String(fetchMock.mock.calls[0]![0])).not.toContain('split=')
})

// from/to are the aliases; since/until are the field names.
it('sends the window as from/to, not since/until', async () => {
  respondWith(DATA)
  await fetchChartData(1, { from: '2026-01-01', to: '2026-08-31' })
  const url = String(fetchMock.mock.calls[0]![0])
  expect(url).toContain('from=2026-01-01')
  expect(url).toContain('to=2026-08-31')
  expect(url).not.toContain('since=')
})

it('echoes /data resolved arguments into /cell verbatim', async () => {
  const data = { ...DATA, grain: 'quarter', split: 'category', currency: 'GBP',
                 since: '2026-01-01', until: '2026-06-30' }
  respondWith(CELL)
  await fetchCell(1, '2026-01-01', 'software', cellArgs(data as ChartData))
  const url = String(fetchMock.mock.calls[0]![0])
  for (const part of ['grain=quarter', 'split=category', 'currency=GBP',
                      'from=2026-01-01', 'to=2026-06-30', 'period=2026-01-01',
                      'split_value=software']) {
    expect(url).toContain(part)
  }
})

it('omits split_value for the unlabelled bucket', async () => {
  respondWith(CELL)
  await fetchCell(1, '2026-01-01', null, {})
  expect(String(fetchMock.mock.calls[0]![0])).not.toContain('split_value=')
})
```

- [ ] **Step 8: Run both specs and lint**

Run: `cd /Users/john/projects/syncthing/agent-lxc/library-4b/frontend && npx vitest run src/spending src/api/__tests__/spending.spec.ts && npm run type-check`
Expected: PASS.

- [ ] **Step 9: Mutation-check the split trap**

In `windowQuery`, change the guard to `if (args.split) query.split = args.split`. Run the spec. Expected: "sends split= when the split is cleared" goes RED. Restore.

- [ ] **Step 10: Commit**

```bash
git add frontend/src/spending frontend/src/api/spending.ts frontend/src/api/__tests__/spending.spec.ts
git commit -m "feat(spending): the API client and exact money arithmetic"
```

---

### Task 2: The palette — the shared module, the fold, slot assignment

**Files:**
- Create: `frontend/src/utils/splitPalette.ts` (see the ownership note below)
- Create: `frontend/src/spending/palette.ts`, `frontend/src/spending/__tests__/palette.spec.ts`

**Interfaces:**
- Consumes: `toCents` from `@/spending/money`; `SplitValue`, `Cell` from `@/api/spending`; `SPLIT_PALETTE`, `deriveSlot`, `resolveSplitColour` from `@/utils/splitPalette`.
- Produces: `bands(splits, cells): Band[]` and `interface Band { value: string | null | typeof OTHER_VALUE; label: string; light: string; dark: string; totalCents: number; members: SplitValue[]; isOther: boolean }`, plus `OTHER_VALUE` and `OTHER_COLOUR`. Tasks 3, 4, 6, 7, 9 and 10 consume `bands()`; none of them derives a colour itself.

Read spec §4.12 before starting.

**Ownership note — `splitPalette.ts` belongs to plan 4c**, which is building the
facet-vocabulary panel in a parallel worktree and needs the same value-to-colour
mapping (§2.5 has both plans deriving a slot when `colour` is null). Create it
here with **exactly** the contract and hex values below so this branch builds
and tests on its own; whichever branch merges second deletes its copy and keeps
the other. Because the values are identical the reconciliation is a deletion,
not a merge. Put that fact in the module's docstring.

```ts
export interface PaletteSlot { name: string; light: string; dark: string }
export const SPLIT_PALETTE: readonly PaletteSlot[]     // the six slots below
export function deriveSlot(key: string): PaletteSlot   // FNV-1a over the key
export function resolveSplitColour(stored: string | null, key: string, dark: boolean): string
```

`resolveSplitColour` has three cases: a null `stored` derives a slot from the
key and returns that slot's step for the current theme; a `stored` value
matching a slot's **light** hex resolves to that slot and returns its step for
the theme (the database holds one hex, which is what stops an owner's override
being a light-mode colour on a dark chart); anything else is returned verbatim.

| slot | name | light | dark |
| --- | --- | --- | --- |
| 1 | Blue | `#1283dc` | `#5791ca` |
| 2 | Orange | `#ff6f42` | `#b93b09` |
| 3 | Green | `#51ae7f` | `#19825f` |
| 4 | Indigo | `#4423da` | `#584fcc` |
| 5 | Plum | `#993375` | `#ed3297` |
| 6 | Olive | `#876708` | `#b08923` |

Validated against this app's own chart surfaces (`.card` is `bg-white` /
`dark:bg-gray-800`) on the **all-pairs** pairlist in both modes: worst CVD ΔE
9.9 light and 9.3 dark, normal-vision 19.8 and 17.2. Do not change a hex.

- [ ] **Step 1: Write the failing spec**

`frontend/src/spending/__tests__/palette.spec.ts`. The fold, ordering and
stored-colour assertions below were executed against a reference implementation
before this plan was written.

```ts
import { describe, expect, it } from 'vitest'
import { bands, OTHER_VALUE } from '../palette'
import { SPLIT_PALETTE, deriveSlot } from '@/utils/splitPalette'

const S = (value: string | null, label: string, colour: string | null = null) => ({ value, label, colour })
const C = (split_value: string | null, total: string) =>
  ({ split_value, total, period: '2026-01-01', payments: 1 })

describe('palette', () => {
  it('gives an unsplit chart no bands at all', () => {
    expect(bands([], [C(null, '10.00')])).toEqual([])
  })

  // The whole point of a hash-derived slot: the same key is the same colour
  // wherever it appears, including in 4c's vocabulary panel.
  it('gives a value the slot its key derives, when nothing collides', () => {
    const b = bands([S('hosting', 'Hosting')], [C('hosting', '10.00')])
    expect(b[0]!.light).toBe(deriveSlot('hosting').light)
  })

  // De-collision: two bands must never render the same colour.
  it('never gives two bands the same colour', () => {
    const keys = ['hosting', 'licences', 'tools', 'training', 'postage', 'freight']
    const b = bands(keys.map((k) => S(k, k)), keys.map((k) => C(k, '10.00')))
    const lights = b.map((x) => x.light)
    expect(new Set(lights).size).toBe(lights.length)
  })

  it('is deterministic under input reordering', () => {
    const keys = ['hosting', 'licences', 'tools', 'training']
    const cells = keys.map((k) => C(k, '10.00'))
    const one = bands(keys.map((k) => S(k, k)), cells).map((x) => [x.value, x.light])
    const two = bands([...keys].reverse().map((k) => S(k, k)), cells).map((x) => [x.value, x.light])
    expect(one).toEqual(two)
  })

  it('folds the tail past six into one Other bucket', () => {
    const keys = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h']
    const cells = keys.map((k, i) => C(k, `${100 - i * 10}.00`))
    const b = bands(keys.map((k) => S(k, k.toUpperCase())), cells)
    expect(b.map((x) => (x.value === OTHER_VALUE ? 'OTHER' : x.value)))
      .toEqual(['a', 'b', 'c', 'd', 'e', 'f', 'OTHER'])
    expect(b.at(-1)!.label).toBe('Other (2)')
    expect(b.at(-1)!.members.map((m) => m.value)).toEqual(['g', 'h'])
  })

  // §9.2's promise: the stack height is the total, whatever the split does.
  it('preserves the total across the fold', () => {
    const keys = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h']
    const cells = keys.map((k, i) => C(k, `${100 - i * 10}.11`))
    const b = bands(keys.map((k) => S(k, k)), cells)
    const expected = cells.reduce((n, c) => n + Math.round(parseFloat(c.total) * 100), 0)
    expect(b.reduce((n, x) => n + x.totalCents, 0)).toBe(expected)
  })

  // Forced tie: a fold that depends on input order is a fold that flickers.
  it('folds deterministically when every total is equal', () => {
    const keys = ['a', 'b', 'c', 'd', 'e', 'f', 'g']
    const cells = keys.map((k) => C(k, '10.00'))
    const names = (ks: string[]) =>
      bands(ks.map((k) => S(k, k)), cells).map((x) => (x.value === OTHER_VALUE ? 'OTHER' : x.value))
    expect(names(keys)).toEqual(names([...keys].reverse()))
    expect(names(keys)).toEqual(['a', 'b', 'c', 'd', 'e', 'f', 'OTHER'])
  })

  it('keeps the unlabelled bucket, sorted last, with the API label', () => {
    const b = bands([S(null, 'No category'), S('a', 'A')], [C(null, '5.00'), C('a', '9.00')])
    expect(b.map((x) => x.value)).toEqual(['a', null])
    expect(b[1]!.label).toBe('No category')
  })

  // A refund can exceed the payments in its bucket.
  it('ranks a negative net last without dropping it', () => {
    const b = bands([S('a', 'A'), S('b', 'B')], [C('a', '-5.00'), C('b', '20.00')])
    expect(b.map((x) => x.totalCents)).toEqual([-500, 2000])
  })

  // A stored colour claims its slot before any derived one is handed out.
  it('honours a stored colour and does not hand its slot out twice', () => {
    const claimed = SPLIT_PALETTE[2]!
    const b = bands([S('a', 'A', claimed.light), S('b', 'B')], [C('a', '1.00'), C('b', '2.00')])
    expect(b[0]!.light).toBe(claimed.light)
    expect(b[1]!.light).not.toBe(claimed.light)
  })

  it('keeps a split value that has no cells, as a real zero', () => {
    const b = bands([S('a', 'A'), S('b', 'B')], [C('a', '10.00')])
    expect(b.map((x) => [x.value, x.totalCents])).toEqual([['a', 1000], ['b', 0]])
  })
})
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd /Users/john/projects/syncthing/agent-lxc/library-4b/frontend && npx vitest run src/spending/__tests__/palette.spec.ts`
Expected: FAIL — cannot resolve `../palette`.

- [ ] **Step 3: Write both modules**

`splitPalette.ts` to the contract above. FNV-1a, exactly:

```ts
export function fnv1a(key: string): number {
  let h = 0x811c9dc5
  for (let i = 0; i < key.length; i++) {
    h ^= key.charCodeAt(i)
    h = Math.imul(h, 0x01000193) >>> 0
  }
  return h
}
export const deriveSlot = (key: string): PaletteSlot =>
  SPLIT_PALETTE[fnv1a(key) % SPLIT_PALETTE.length]!
```

Then `palette.ts`. The fold and ordering, then the assignment:

```ts
export const OTHER_VALUE = Symbol.for('spending.other')
export const OTHER_COLOUR = { light: '#9ca3af', dark: '#9ca3af' } as const  // gray-400
const MAX_BANDS = SPLIT_PALETTE.length

const NULL_KEY = '\u0000null'
const keyOf = (v: string | null): string => (v === null ? NULL_KEY : v)

// `null` sorts LAST: "no value for this facet" reads as the trailing bucket.
const cmpKey = (a: string | null, b: string | null): number =>
  a === null ? (b === null ? 0 : 1) : b === null ? -1 : a < b ? -1 : a > b ? 1 : 0
```

`bands()` then: sum `cells` into cents per `split_value`; rank by total
descending with **ties broken by key ascending**; keep the first `MAX_BANDS`;
fold the rest into `Other`; re-sort the survivors by key ascending with `null`
last; and assign colours in two passes over that key order —

```ts
// Pass 1: stored colours claim their slot. Pass 2: derived slots take their
// preferred slot, or walk forward to the next free one.
const taken = new Set<number>()
// ... pass 1 over survivors with a non-null `colour`, matching SPLIT_PALETTE
//     by LIGHT hex; a hex matching no slot is used verbatim and claims nothing.
// ... pass 2: let idx = SPLIT_PALETTE.indexOf(deriveSlot(keyOf(value)))
//     while (taken.has(idx)) idx = (idx + 1) % SPLIT_PALETTE.length
```

The walk terminates because `survivors.length <= SPLIT_PALETTE.length`; still,
bound it at `SPLIT_PALETTE.length` iterations rather than trusting that.

Four rules the implementation must not soften:

- **Rank order decides what folds; key order decides the assignment sequence.**
- **Stored colours claim before derived ones**, so an override always wins.
- **Two bands never share a colour.**
- **`null` sorts last.**

- [ ] **Step 4: Run it and watch it pass**

Run: `cd /Users/john/projects/syncthing/agent-lxc/library-4b/frontend && npx vitest run src/spending/__tests__/palette.spec.ts && npm run type-check`
Expected: PASS, 11 tests.

- [ ] **Step 5: Mutation-check the three rules**

Each must red the named test; restore after each.

1. Delete the de-collision walk (always use the derived slot). → "never gives two bands the same colour" reds. Use the six keys in that test; they are chosen to collide.
2. Change the tie-break to `|| 0`. → "folds deterministically when every total is equal" reds.
3. Run pass 2 before pass 1. → "honours a stored colour" reds.

If any stays green, the test cannot detect the defect its name claims — fix the test, not the check.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/utils/splitPalette.ts frontend/src/spending/palette.ts frontend/src/spending/__tests__/palette.spec.ts
git commit -m "feat(spending): the shared split palette, the fold and slot assignment"
```

### Task 3: `SpendingChart.vue`

**Files:**
- Create: `frontend/src/components/spending/SpendingChart.vue`
- Create: `frontend/src/components/spending/__tests__/SpendingChart.spec.ts`

**Interfaces:**
- Consumes: `bands()` from `@/spending/palette`; `ChartData`, `Cell` from `@/api/spending`.
- Produces: props `{ data: ChartData; bands: Band[]; hidden?: Set<string | null | symbol>; compact?: boolean }`; emits `cell: [period: string, splitValue: string | null | typeof OTHER_VALUE]`.

The four semantic constraints in spec §4.4 are fixed and are not aesthetic choices. Register **`CategoryScale`, not `TimeScale`** — the x-axis is uniform periods, and a category scale makes "nothing is 2px wide because two invoices landed three days apart" true by construction rather than by configuration.

- [ ] **Step 1: Write the failing spec**

Mock `vue-chartjs` as `SeriesChartTile.spec.ts` already does, and assert on the **options and datasets object** handed to the mocked `<Bar>`, which is the component's real output:

```ts
vi.mock('vue-chartjs', () => ({
  Bar: { name: 'Bar', props: ['data', 'options'], template: '<canvas />' },
}))
```

The assertions:

```ts
it('stacks both axes so the stack height is the total', () => {
  const options = optionsOf(mountChart())
  expect(options.scales.x.stacked).toBe(true)
  expect(options.scales.y.stacked).toBe(true)
})

// A refund exceeding its bucket's payments draws below the baseline; an axis
// that starts elsewhere hides the sign.
it('always includes zero on the y axis', () => {
  const options = optionsOf(mountChart(WITH_NEGATIVE_CELL))
  expect(options.scales.y.beginAtZero).toBe(true)
})

it('uses a category x axis, so every period is the same width', () => {
  // A TimeScale would size bars by date distance. The labels are the periods.
  expect(chartDataOf(mountChart()).labels).toEqual(['2026-06-01', '2026-07-01', '2026-08-01'])
})

// The chart takes `bands` as a prop and must not re-derive a colour: the
// assignment (fold, de-collision, stored overrides) belongs to palette.ts.
it('draws one dataset per band, in band order, with the band colour', () => {
  const bands = BANDS   // built by the fixture via bands(splits, cells)
  const datasets = chartDataOf(mountChart()).datasets
  expect(datasets.map((d) => d.label)).toEqual(bands.map((b) => b.label))
  expect(datasets.map((d) => d.backgroundColor)).toEqual(bands.map((b) => b.light))
})

// The 2px surface gap is the separator; a border around a mark is not.
it('separates touching segments with a surface-coloured gap, not a stroke', () => {
  const datasets = chartDataOf(mountChart()).datasets
  expect(datasets.every((d) => d.borderWidth === 2)).toBe(true)
  expect(datasets.every((d) => d.borderColor === '#ffffff')).toBe(true)
})

it('caps bar thickness so a two-period chart does not draw slabs', () => {
  expect(chartDataOf(mountChart()).datasets[0]!.maxBarThickness).toBe(24)
})

it('emits the cell that was clicked, with its raw split value', () => {
  const wrapper = mountChart()
  clickBar(wrapper, { datasetIndex: 0, index: 2 })
  expect(wrapper.emitted('cell')![0]).toEqual(['2026-08-01', 'hosting'])
})

it('emits the Other symbol for the folded band, never a fake split value', () => {
  const wrapper = mountChart()
  clickBar(wrapper, { datasetIndex: 2, index: 2 })
  expect(wrapper.emitted('cell')![0]).toEqual(['2026-08-01', OTHER_VALUE])
})

// Isolation is a display filter and must not change the assignment.
it('hides a band without recolouring the survivors', () => {
  const before = chartDataOf(mountChart(DATA)).datasets
  const after = chartDataOf(mountChart(DATA, new Set(['licences']))).datasets
  expect(after.map((d) => d.label)).toEqual(['Hosting', 'Other (2)'])
  // Every survivor keeps the exact colour it had before the filter.
  for (const dataset of after) {
    const was = before.find((d) => d.label === dataset.label)!
    expect(dataset.backgroundColor).toBe(was.backgroundColor)
  }
})

it('renders a single unsplit series with no legend datasets to name', () => {
  // `bands()` returns [] for an unsplit chart, so the chart draws one series
  // in the first palette slot and the legend renders nothing.
  const datasets = chartDataOf(mountChart(UNSPLIT)).datasets
  expect(datasets).toHaveLength(1)
  expect(datasets[0]!.backgroundColor).toBe(SPLIT_PALETTE[0]!.light)
})
```

- [ ] **Step 2: Run and watch it fail**

Run: `cd /Users/john/projects/syncthing/agent-lxc/library-4b/frontend && npx vitest run src/components/spending/__tests__/SpendingChart.spec.ts`
Expected: FAIL — component missing.

- [ ] **Step 3: Implement**

```ts
import { Bar } from 'vue-chartjs'
import { BarElement, CategoryScale, Chart as ChartJS, LinearScale, Tooltip } from 'chart.js'
ChartJS.register(CategoryScale, LinearScale, BarElement, Tooltip)
```

Requirements the spec above pins, plus these which it does not:

- **The rounded data-end goes on the outermost segment only**, square at the baseline: compute `borderRadius` per bar via a scriptable option that returns `{ topLeft: 4, topRight: 4 }` only for the topmost non-zero segment of that stack (and the *bottom* corners for a stack whose net is negative). A blanket `borderRadius` rounds every interior segment and reads as separate pills.
- **The surface colour is theme-dependent**: `#ffffff` light, `#1f2937` dark (`gray-800`, the `.card` background). Get the theme from **`useDark({ selector: 'html' })`** (`@vueuse/core`, already a dependency) — the same call `components/layout/ThemeToggle.vue` uses, so there is one source of truth. It is reactive, so a computed over it re-renders the chart on the toggle with no watcher and no `classList` polling of your own. Use the same flag to pick each band's `light`/`dark` hex.
- **Hover tooltip on every bar**, showing the band label, the period and `formatMoney(cell.total, data.currency)`. Keyboard focus shows the same — the chart is a `<canvas>`, so the accessible path is the drill panel, which lists the same numbers as text (§10.5).
- **Hold the previous render at reduced opacity on refetch.** No skeleton flash, no layout jump.
- The `compact` prop drops the y-axis ticks and shortens the x labels for the board card; it never changes the data.

- [ ] **Step 4: Run and watch it pass**

Run: same command. Expected: PASS.

- [ ] **Step 5: Mutation-check the axis constraints**

Set `beginAtZero: false`. Expected: "always includes zero" goes RED. Restore. Then swap `CategoryScale` for `TimeScale` and confirm the labels assertion reds. Restore.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/spending/SpendingChart.vue frontend/src/components/spending/__tests__/SpendingChart.spec.ts
git commit -m "feat(spending): the stacked-bar chart"
```

---

### Task 4: `SpendingLegend.vue`

**Files:**
- Create: `frontend/src/components/spending/SpendingLegend.vue` + `__tests__/SpendingLegend.spec.ts`

**Interfaces:**
- Produces: props `{ bands: Band[]; hidden: Set<string | null | symbol>; currency: string; compact?: boolean }`; emits `isolate: [key]`, `exclude: [key]`, `reset: []`.

- [ ] **Step 1: Write the failing spec**

```ts
it('renders a swatch, a label and a value for every band', () => { … })

// §4.7: an isolate that rewrote the headline would break §9.2's one promise,
// in the direction that looks most plausible.
it('emits isolate on click and exclude on modifier-click', async () => {
  await rowOf(wrapper, 'Hosting').trigger('click')
  expect(wrapper.emitted('isolate')![0]).toEqual(['hosting'])
  await rowOf(wrapper, 'Licences').trigger('click', { metaKey: true })
  expect(wrapper.emitted('exclude')![0]).toEqual(['licences'])
})

it('marks a hidden band as hidden without removing it from the legend', () => {
  // Removing it would leave no way to bring it back.
  const row = rowOf(mountLegend(new Set(['licences'])), 'Licences')
  expect(row.attributes('aria-pressed')).toBe('false')
  expect(row.exists()).toBe(true)
})

it('names the folded values in the Other row so they are still identifiable', () => { … })

it('renders nothing for an unsplit chart', () => {
  // One colour needs no legend; the chart's name already says what is plotted.
  expect(mountLegend([], new Set()).find('[data-testid="spending-legend"]').exists()).toBe(false)
})
```

- [ ] **Step 2–4: fail, implement, pass.** Rows are `<button aria-pressed>`, not divs, so the isolate state is announced and the row is keyboard-operable. Amounts use `tabular-nums` (they align vertically); the headline figure elsewhere does not.

- [ ] **Step 5: Mutation-check.** Make a hidden band render `v-if="!hidden"`. Expected: "marks a hidden band as hidden without removing it" goes RED. Restore.

- [ ] **Step 6: Commit** — `feat(spending): the split legend`

---

### Task 5: `SpendingFooter.vue`

**Files:**
- Create: `frontend/src/components/spending/SpendingFooter.vue` + `__tests__/SpendingFooter.spec.ts`

**Interfaces:**
- Produces: props `{ data: ChartData }`; emits `bucket: [bucket: FooterBucket, amountKind?: string]`.

Read spec §4.5. Four of its requirements each correspond to a defect the engine's own review found, and each gets its own test.

- [ ] **Step 1: Write the failing spec**

```ts
// A refund is IN the total and lowers it. Under "excluded from the total" it
// would read as money the chart ignored — the opposite of what happened.
it('puts netted refunds in the header block, never under excluded', () => {
  const wrapper = mountFooter(WITH_REFUND)
  expect(headerBlock(wrapper).text()).toContain('1 refund')
  expect(excludedBlock(wrapper).text()).not.toContain('refund')
})

// Excluded means correctly not spending; undecided means not yet decided.
it('puts unclassified and uncategorised under needs attention', () => {
  const wrapper = mountFooter(FULL)
  expect(attentionBlock(wrapper).text()).toContain('unclassified')
  expect(attentionBlock(wrapper).text()).toContain('uncategorised')
  expect(excludedBlock(wrapper).text()).not.toContain('uncategorised')
})

it('renders unaccounted under needs attention when it is not empty', () => {
  // It should always be empty. When it is not, this is the money in the hole.
  expect(attentionBlock(mountFooter(WITH_UNACCOUNTED)).text()).toContain('unaccounted')
})

// An unconvertible payment and an equal unconvertible refund net to 0.00
// across two documents, which without the count reads as "nothing missing".
it('always renders documents beside an unconvertible amount', () => {
  const row = unconvertibleRow(mountFooter(ZERO_NET_UNCONVERTIBLE))
  expect(row.text()).toContain('0.00')
  expect(row.text()).toContain('2 documents')
})

it('labels a null unconvertible currency and sorts it last', () => {
  const rows = unconvertibleRows(mountFooter(WITH_NULL_CURRENCY))
  expect(rows.at(-1)!.text()).toContain('No currency')
})

// §2.4: these two have no bucket route. Wiring them up is a 422.
it('renders refund_count and unconvertible documents as plain figures', () => {
  const wrapper = mountFooter(FULL)
  expect(refundFigure(wrapper).element.tagName).not.toBe('BUTTON')
  expect(unconvertibleFigure(wrapper).element.tagName).not.toBe('BUTTON')
})

it('opens the five drillable buckets, and names the kind for excluded', async () => {
  const wrapper = mountFooter(FULL)
  await bucketButton(wrapper, 'uncategorised').trigger('click')
  expect(wrapper.emitted('bucket')![0]).toEqual(['uncategorised', undefined])
  await bucketButton(wrapper, 'coverage_limit').trigger('click')
  expect(wrapper.emitted('bucket')![1]).toEqual(['excluded', 'coverage_limit'])
})

// An absent field and an empty one are different claims, and only one of them
// is "nothing was excluded".
it('renders every block even when its groups are null', () => {
  const wrapper = mountFooter(EMPTY_FOOTER)
  expect(excludedBlock(wrapper).exists()).toBe(true)
  expect(attentionBlock(wrapper).exists()).toBe(true)
})
```

- [ ] **Step 2–4: fail, implement, pass.**

The visual treatment is the one place this view spends its boldness (§4.13's
note on the footer): a typeset accounting statement, not a row of stat chips —
three labelled blocks, hairline rules between them, `tabular-nums` amounts
right-aligned to a common edge, labels in the `.filter-label` uppercase-xs
recipe. The header line reads
`<total> across <payments> payments from <documents> documents`.

**The three meanings of `documents` must never be added or presented as parts of
one whole.** `ChartData.documents` counts payment-group members; a footer
group's `documents` counts canonical rows; merged `unconvertible.documents` is a
summed upper bound. Each is correct. Put them in different blocks and do not
draw a total across them.

- [ ] **Step 5: Mutation-check.** Move `uncategorised` into the excluded block. Expected: the "needs attention" test goes RED. Restore.

- [ ] **Step 6: Commit** — `feat(spending): the footer accounting`

---

### Task 6: The drill panel — one shell, three bodies

**Files:**
- Create: `SpendingDrillPanel.vue`, `DrillCellBody.vue`, `DrillBucketBody.vue`, `DrillOtherBody.vue` under `frontend/src/components/spending/`
- Create: `__tests__/SpendingDrillPanel.spec.ts`, `__tests__/DrillCellBody.spec.ts`, `__tests__/DrillBucketBody.spec.ts`, `__tests__/DrillOtherBody.spec.ts`

**Interfaces:**
- Produces: `SpendingDrillPanel` props `{ open: boolean; title: string; sheet: boolean }`, emits `close: []`, default slot for the body. The shell owns the `<dialog>`, focus, Escape, and the side-panel-vs-bottom-sheet presentation; it owns **no** data fetching.
- `DrillCellBody` props `{ chartId: number; period: string; splitValue: string | null; args: ChartArgs; chartName: string }`.
- `DrillBucketBody` props `{ chartId: number; bucket: FooterBucket; amountKind?: string; args: ChartArgs }`.
- `DrillOtherBody` props `{ period: string; members: SplitValue[]; cells: Cell[]; currency: string }`, emits `pick: [value: string | null]`.

The shell is a native `<dialog>` as `SearchModal.vue` and `ConfirmDialog.vue` already are — focus containment, Escape and an inert background come with it rather than being hand-rolled a third time.

- [ ] **Step 1: Write the shell's failing spec**

```ts
it('renders as a side panel when sheet is false and a bottom sheet when true', () => {
  // Assert the DOM outcome — the dialog's data-presentation — not a class list.
  expect(mountPanel({ sheet: false }).get('dialog').attributes('data-presentation')).toBe('panel')
  expect(mountPanel({ sheet: true }).get('dialog').attributes('data-presentation')).toBe('sheet')
})

it('emits close on Escape and on the close button', async () => { … })

it('titles itself from the prop, so an unsplit chart can pass its own name', () => {
  // CellOutBody.label is "" for an unsplit chart, and "" is not a title.
  expect(mountPanel({ title: 'All spending' }).get('[data-testid="drill-title"]').text())
    .toBe('All spending')
})
```

- [ ] **Step 2: Write `DrillCellBody`'s failing spec** — the three contracts that bite

```ts
it('sends /data resolved arguments plus the cell period, verbatim', async () => {
  mountCellBody({ args: cellArgs(DATA), period: '2026-08-01', splitValue: 'hosting' })
  await flushPromises()
  expect(vi.mocked(fetchCell)).toHaveBeenCalledWith(
    7, '2026-08-01', 'hosting',
    { grain: 'month', split: 'category', currency: 'EUR', from: '2026-01-01', to: '2026-08-31' },
  )
})

// The server's 422 names the correct boundary. An empty panel does not.
it('renders the 422 detail rather than an empty panel', async () => {
  vi.mocked(fetchCell).mockRejectedValue(
    new ApiError(422, 'period 2026-08-15 is not the start of a month; use 2026-08-01'),
  )
  const wrapper = mountCellBody()
  await flushPromises()
  expect(wrapper.text()).toContain('use 2026-08-01')
  expect(wrapper.find('[data-testid="drill-empty"]').exists()).toBe(false)
})

// The panel is where a wrong merge is noticed, so it must add up to the bar.
it('shows each payment total and their sum equals the cell total', async () => {
  const wrapper = await mountedCellBody(CELL_WITH_MERGE)
  const shown = paymentTotals(wrapper).map(toCents).reduce((a, b) => a + b, 0)
  expect(fromCents(shown)).toBe(CELL_WITH_MERGE.total)
})

// A merged pair doubles the document sum; that is the merge this panel exposes.
it('never presents a sum of document amounts as the total', async () => {
  const wrapper = await mountedCellBody(CELL_WITH_MERGE)   // documents sum to 2x the total
  expect(wrapper.text()).not.toContain(fromCents(2 * toCents(CELL_WITH_MERGE.total)))
})

// A hand-made MERGE override can pull an amountless document into a group.
it('renders a document with no amount and no currency', async () => {
  const wrapper = await mountedCellBody(CELL_WITH_AMOUNTLESS_DOCUMENT)
  expect(wrapper.text()).toContain('No amount recorded')
})
```

- [ ] **Step 3: Write `DrillBucketBody`'s failing spec**

```ts
it('shows the page size against the bucket total, never a silent truncation', async () => {
  vi.mocked(fetchFooterBucket).mockResolvedValue({ bucket: 'uncategorised', total: 340,
                                                   documents: page(100) })
  expect((await mountedBucketBody()).text()).toContain('100 of 340')
})

it('requires amount_kind for the excluded bucket', async () => {
  await mountedBucketBody({ bucket: 'excluded', amountKind: 'coverage_limit' })
  expect(vi.mocked(fetchFooterBucket).mock.calls[0]![2]).toMatchObject({ amount_kind: 'coverage_limit' })
})

it('caps its page size at the server maximum', async () => {
  await mountedBucketBody()
  expect(vi.mocked(fetchFooterBucket).mock.calls[0]![2]!.limit).toBeLessThanOrEqual(100)
})
```

- [ ] **Step 4: Write `DrillOtherBody`'s failing spec**

```ts
// The folded values' totals for this period are already in /data's cells, so
// this step costs no request.
it('lists the folded values for the period without fetching anything', () => {
  const wrapper = mountOtherBody({ period: '2026-08-01', members: TAIL, cells: CELLS })
  expect(rowLabels(wrapper)).toEqual(['Insurance', 'Training', 'Postage'])
  expect(rowAmounts(wrapper)).toEqual(['EUR 180.00', 'EUR 121.10', 'EUR 64.50'])
  expect(vi.mocked(fetchCell)).not.toHaveBeenCalled()
})

it('emits the raw value when a row is picked, so /cell can round-trip it', async () => {
  await rowOf(wrapper, 'Training').trigger('click')
  expect(wrapper.emitted('pick')![0]).toEqual(['training'])
})

it('shows only this period, not the whole window', () => {
  // The bar that was clicked is one period; the fold's members have cells in
  // others too, and listing those would not add up to the segment.
  expect(rowAmounts(mountOtherBody({ period: '2026-08-01', cells: CELLS_ACROSS_MONTHS })))
    .toEqual(['EUR 180.00'])
})
```

- [ ] **Step 5: Implement all four, then run**

Run: `cd /Users/john/projects/syncthing/agent-lxc/library-4b/frontend && npx vitest run src/components/spending`
Expected: PASS.

`DrillCellBody` reuses `FacetEditor.vue` (props `documentId`, `facets`, `labels`) and `PaymentGroup.vue` (prop `documentId`) inline per document, rather than reimplementing either — the correction is made where the problem was noticed.

- [ ] **Step 6: Mutation-check the period filter**

In `DrillOtherBody`, drop the `cell.period === props.period` filter. Expected: "shows only this period" goes RED. Restore. This one matters: without the filter the panel's numbers do not add up to the segment that opened it.

- [ ] **Step 7: Commit** — `feat(spending): the drill panel and its three bodies`

---

### Task 7: `SpendingCard.vue`

**Files:**
- Create: `frontend/src/components/spending/SpendingCard.vue` + `__tests__/SpendingCard.spec.ts`

**Interfaces:**
- Produces: props `{ chart: Chart; data: ChartData | null; error: string | null; busy: boolean; canMoveUp: boolean; canMoveDown: boolean; today?: string }`; emits `edit`, `delete`, `move-up`, `move-down`.
  `today` defaults to the real current date and exists so "the most recent **complete** bucket" is
  testable — without pinning it, that test asserts something different every day it runs.

Anatomy is settled (spec §4.13): name and overflow menu, then the headline figure, then the compact chart, then the legend ribbon, then a needs-attention line when the footer has one.

- [ ] **Step 1: Write the failing spec**

```ts
// A partial month against a full one is the comparison that is always wrong
// and never looks it.
it('headlines the most recent COMPLETE bucket, not the current partial one', () => {
  const wrapper = mountCard({ today: '2026-08-14', cells: JUNE_JULY_AUGUST })
  expect(headline(wrapper).text()).toContain('July')
  expect(headline(wrapper).text()).not.toContain('August')
})

it('still draws the partial bucket on the chart', () => {
  expect(chartLabels(mountCard({ today: '2026-08-14', cells: JUNE_JULY_AUGUST })))
    .toContain('2026-08-01')
})

it('compares against the bucket before it, exactly', () => {
  // 1284.50 - 1142.20 in floats is 142.29999999999998.
  expect(delta(mountCard(TWO_BUCKETS)).text()).toContain('142.30')
})

// Spending rising is not good or bad without knowing what it was spent on.
it('does not colour the delta as good or bad', () => {
  const el = delta(mountCard(RISING)).element as HTMLElement
  expect(el.className).not.toMatch(/text-(red|green)-/)
})

it('renders a needs-attention line only when the footer has one', () => {
  expect(attention(mountCard(WITH_UNCATEGORISED)).text()).toContain('3 documents uncategorised')
  expect(attention(mountCard(CLEAN)).exists()).toBe(false)
})

it('renders its own error without hiding the rest of the board', () => {
  expect(mountCard({ data: null, error: 'Could not load this chart.' }).text())
    .toContain('Could not load this chart.')
})

it('keeps edit and delete in the overflow menu, not on the card face', () => {
  const wrapper = mountCard(READY)
  expect(wrapper.find('[data-testid="spending-card-delete"]').exists()).toBe(false)
})

// Spec §4.2 puts move up/down IN the overflow menu, alongside edit and delete —
// §10.3 #5 is "cards show data, not six controls each", and two more face
// buttons per card is exactly that. So the menu must be opened first. This is
// still the accessible reorder path: AppPopover is keyboard-operable and
// returns focus, and it is the path e2e asserts on all three viewport projects.
it('offers move up and move down as real buttons in the menu, disabled at the ends', async () => {
  const wrapper = mountCard({ canMoveUp: false, canMoveDown: true })
  await openOverflowMenu(wrapper)
  expect(moveUp(wrapper).attributes('disabled')).toBeDefined()
  expect(moveDown(wrapper).attributes('disabled')).toBeUndefined()
})

it('keeps the card face free of reorder controls until the menu is opened', () => {
  const wrapper = mountCard({ canMoveUp: true, canMoveDown: true })
  expect(wrapper.find('[data-testid="spending-card-move-up"]').exists()).toBe(false)
})
```

- [ ] **Step 2–4: fail, implement, pass.**

"Most recent complete bucket" means: the last cell period strictly before the
period containing today, at `data.grain`. Compute the current period's start the
same way the server does (`date_trunc`) rather than by string slicing, so `week`
and `quarter` are right.

- [ ] **Step 5: Mutation-check.** Change the headline to take the last bucket unconditionally. Expected: the "complete bucket" test goes RED. Restore.

- [ ] **Step 6: Commit** — `feat(spending): the board card`

---

### Task 8: `QuestionDraft.vue` and `SpendingEmptyState.vue`

**Files:**
- Create both under `frontend/src/components/spending/`, plus their specs.

**Interfaces:**
- `QuestionDraft` props `{ currency: string }`, emits `saved: [chart: Chart]`.
- `SpendingEmptyState` props `{ currency: string }`, emits `created: [chart: Chart]`.

Both take the currency rather than choosing one. The board supplies it from the existing
`useCurrencyOptions()` composable (built-ins EUR / GBP / USD, per-machine persisted) and lets it be
changed through the existing `CurrencySelect.vue`. Hardcoding a currency here would be the same
defect §2.2 rejected in the seed migration: a display currency nobody chose.

Drafting has **three** states, not two (spec §4.8). Conflating the last two is the failure the redesign spec §7.5 names.

- [ ] **Step 1: Write the failing draft spec**

```ts
it('shows the rule, the split, the preview and an enabled save when expressible', async () => { … })

it('labels a partial draft an approximation and still allows saving', async () => {
  // expressible: false WITH rule and preview present.
  const wrapper = await drafted({ expressible: false, rule: RULE, preview: PREVIEW,
                                  unknown_terms: ['vibes'] })
  expect(wrapper.text()).toContain('approximation')
  expect(wrapper.text()).toContain('vibes')
  expect(previewChart(wrapper).exists()).toBe(true)
  expect(saveButton(wrapper).attributes('disabled')).toBeUndefined()
})

// The one that matters: an empty rule matches the whole archive, so previewing
// it answers a narrow question with the archive's total.
it('shows NO preview and disables save when every clause was dropped', async () => {
  const wrapper = await drafted({ expressible: false, rule: null, preview: null,
                                  unknown_terms: ['vibes'], message: 'not in the vocabulary: vibes' })
  expect(previewChart(wrapper).exists()).toBe(false)
  expect(saveButton(wrapper).attributes('disabled')).toBeDefined()
  expect(wrapper.text()).toContain('not in the vocabulary')
})

// unknown_terms is model-authored text, already capped server-side.
it('renders unknown terms as text, never as markup', async () => {
  const wrapper = await drafted({ unknown_terms: ['<img src=x onerror=alert(1)>'],
                                  rule: null, preview: null, expressible: false })
  expect(wrapper.find('img').exists()).toBe(false)
  expect(wrapper.text()).toContain('<img src=x onerror=alert(1)>')
})
```

- [ ] **Step 2: Write the failing empty-state spec**

```ts
// §2.2: the seed is the owner clicking it, through the ordinary save path.
it('offers All spending first and pinned', () => {
  expect(proposalLabels(mountEmpty(COUNTS))[0]).toBe('All spending')
})

it('saves All spending as an empty rule split by category', async () => {
  await proposal(wrapper, 'All spending').trigger('click')
  expect(vi.mocked(createChart)).toHaveBeenCalledWith(
    expect.objectContaining({ rule: { all: [] }, default_split: 'category' }),
  )
})

it('proposes facet values with their count and date span', () => {
  expect(proposal(mountEmpty(COUNTS), 'software').text())
    .toContain('15 documents')
})

it('proposes nothing for a value the counts route did not return', () => {
  // A value with no money behind it is absent from the response by construction.
  expect(proposalLabels(mountEmpty([]))).toEqual(['All spending'])
})
```

- [ ] **Step 3–5: fail, implement, pass, mutation-check.** For the mutation check, make the collapsed branch render the preview anyway; the "NO preview" test must red.

- [ ] **Step 6: Commit** — `feat(spending): the draft flow and the empty state`

---

### Task 9: `SpendingBoardView.vue`

**Files:**
- Create: `frontend/src/views/SpendingBoardView.vue` + `frontend/src/views/__tests__/SpendingBoardView.spec.ts`

- [ ] **Step 1: Write the failing spec**

```ts
it('orders cards by ordinal then name, never by document count', () => { … })

it('loads every chart in parallel and renders a failed one inline', async () => {
  vi.mocked(fetchChartData).mockImplementation((id) =>
    id === 2 ? Promise.reject(new ApiError(500, 'boom')) : Promise.resolve(DATA))
  const wrapper = await mountedBoard(THREE_CHARTS)
  expect(cards(wrapper)).toHaveLength(3)
  expect(cardAt(wrapper, 1).text()).toContain('Could not load')
  expect(wrapper.find('[data-testid="board-banner"]').exists()).toBe(false)
})

it('moves a card down and persists only the ordinals that changed', async () => {
  // Charts 1, 2, 3 at ordinals 0, 1, 2. Moving the FIRST card down swaps it
  // with the second, so chart 2 takes ordinal 0 and chart 1 takes ordinal 1.
  // Chart 3 does not move and must not be PATCHed. The two calls have no
  // required order, so sort before comparing.
  const wrapper = await mountedBoard(THREE_CHARTS)
  await moveDown(cardAt(wrapper, 0)).trigger('click')
  const calls = vi.mocked(updateChart).mock.calls
    .map((c) => [c[0], c[1].ordinal])
    .sort((a, b) => Number(a[0]) - Number(b[0]))
  expect(calls).toEqual([[1, 1], [2, 0]])
})

it('shows the empty state when there are no charts, and the board after saving one', async () => { … })

it('caps its list request at the server maximum', async () => {
  await mountedBoard([])
  expect(vi.mocked(listCharts).mock.calls[0]![0]).toBeLessThanOrEqual(100)
})
```

- [ ] **Step 2–4: fail, implement, pass.**

`PageHeader` with the title `Charts`, the draft input in `#controls`. No
`max-w-*` on the view root — the shell owns max width. The grid is
container-queried, not viewport-queried. Drag-reorder uses `sortablejs` (already
a dependency, and already used by `DashboardFieldsEditor.vue`) and calls the
same reorder function the keyboard path does — one code path, two triggers.

- [ ] **Step 5: Mutation-check.** Make the reorder PATCH every chart. Expected: the "only the ordinals that changed" test goes RED. Restore.

- [ ] **Step 6: Commit** — `feat(spending): the board view`

---

### Task 10: `SpendingWorkspaceView.vue` and the measured threshold

**Files:**
- Create: `frontend/src/views/SpendingWorkspaceView.vue` + `frontend/src/views/__tests__/SpendingWorkspaceView.spec.ts`

**The container threshold is measured, not chosen.** Below it the toolbar becomes the summary chip and the panel becomes a bottom sheet.

- [ ] **Step 1: Measure the content column in a real browser**

With the stack up (`make up`, the `e2e` user created, `npm run dev -- --port 5174`),
sign in and record `#app-page` at each e2e project viewport, sidebar expanded and
collapsed. **A container query on `inline-size` evaluates against the container's
content box, not its border box**, so the padding comes off — which is the whole
reason to measure rather than read the viewport. Measured in the plan session
against this shell, on `main@60d6c95`:

| viewport | sidebar | border box | padding | **content box** |
| --- | --- | --- | --- | --- |
| 1280 (chromium) | expanded | 1024 | 32 | **960** |
| 1280 (chromium) | collapsed | 1200 | 32 | **1136** |
| 656 (tablet-webkit) | overlay | 656 | 24 | **608** |
| 375 (mobile-webkit) | overlay | 375 | 16 | **343** |

The method is confirmed against a guard that already ships: `PageHeader`'s merge
is `@5xl` (64rem = 1024px), and these numbers reproduce
`docs/frontend-view-principles.md` §5.1's measured table exactly — 960 is below
1024 so the 1280-expanded row does not merge, 1136 is above it so the
1280-collapsed row does. Reading the border box instead would have predicted the
opposite for the expanded row.

The panel needs ~320px beside a chart that needs ~420px, plus a 24px gap: about
764px of container. **`@3xl` (48rem = 768px)** sits between the measured 608 and
960, with 160px of clearance below the boundary and 192px above, so no project
sits near the edge. Use `@3xl`, declared on the workspace root — not on
`#app-page`, which the shell owns.

Re-measure if the shell's padding changes. Do not convert this to a `lg:` rule:
at a 1280px viewport the column is 960 or 1136 depending on a sidebar the user
collapses independently, and no viewport query can tell those apart.

**Two mechanisms, not one — and the difference is forced by CSS, not chosen.**

The *toolbar* sits in normal flow inside the workspace, so it uses a real CSS
`@container` query at `@3xl` and needs no JavaScript.

The *panel* cannot. `SpendingDrillPanel` is a native `<dialog>` opened with
`showModal()`, which puts it in the **top layer** — it is not a descendant of
the workspace's container, so no `@container` rule can reach it and no custom
property inherits into it. Task 6 therefore takes `sheet` as a resolved boolean
and mirrors it to `data-presentation`, and **this task owns computing it**.

Compute it by observing the **container**, not the viewport:

- `ResizeObserver` on the workspace's own content column, compared against the
  same 48rem threshold the toolbar's `@container` rule uses.
- **Not** `window.innerWidth`, **not** `matchMedia`, **not** a `lg:` class. At a
  1280px viewport the column is 960px with the sidebar expanded and 1136px with
  it collapsed — a viewport query cannot tell those apart, and getting this
  wrong reintroduces exactly the defect this app has already been caught by
  twice.
- Define 48rem once as a named constant and use it for both the observer and,
  via a comment, the CSS rule — so the two cannot drift silently.

The e2e guard asserts the observable outcome (docked right when wide, docked to
the bottom when narrow), which is what a user experiences and what can be proved
to go red.

- [ ] **Step 2: Write the failing spec**

```ts
it('sends the toolbar through PageHeader controls, not a band of its own', () => { … })

// The panel's presentation follows the CONTENT COLUMN, not the viewport: at a
// 1280px viewport the column is 960px expanded and 1136px collapsed.
it('opens the panel as a sheet when the content column is below the threshold', async () => { … })
it('opens it as a side panel when the column is above it, at the same viewport', async () => { … })

it('loads the chart by id rather than paging the list', async () => {
  await mountedWorkspace()
  expect(vi.mocked(fetchChart)).toHaveBeenCalledWith(7)
  expect(vi.mocked(listCharts)).not.toHaveBeenCalled()
})

it('refetches when a toolbar control changes, and never clamps the axis instead', async () => {
  // §10.3 #2: the range filters the data, so the headline and the drawing
  // can never disagree.
  await changeRange(wrapper, '2026-03-01', '2026-06-30')
  expect(vi.mocked(fetchChartData).mock.calls.at(-1)![1])
    .toMatchObject({ from: '2026-03-01', to: '2026-06-30' })
})

it('sends split= when the split is turned off, so the default does not return', async () => {
  await selectSplit(wrapper, '')
  expect(vi.mocked(fetchChartData).mock.calls.at(-1)![1]).toMatchObject({ split: '' })
})

it('opens the panel on a bar click with /data echoed arguments', async () => { … })

it('opens the Other body for the folded segment, not a /cell call', async () => {
  await clickBand(wrapper, OTHER_VALUE)
  expect(wrapper.find('[data-testid="drill-other"]').exists()).toBe(true)
  expect(vi.mocked(fetchCell)).not.toHaveBeenCalled()
})

it('opens a footer bucket in the same panel shell', async () => { … })

// §4.7: isolation must not touch the number the API reported.
it('keeps the headline total when a legend entry is isolated', async () => {
  const before = headlineTotal(wrapper)
  await isolate(wrapper, 'hosting')
  expect(headlineTotal(wrapper)).toBe(before)
  expect(selectionLine(wrapper).text()).toContain('Hosting')
})
```

- [ ] **Step 3–4: fail, implement, pass.**

- [ ] **Step 5: Commit** — `feat(spending): the workspace view`

---

### Task 11: Routes, and retiring the old board

**Files:**
- Modify: `frontend/src/router/index.ts`
- Delete: `frontend/src/views/ChartsView.vue`, `frontend/src/views/__tests__/ChartsView.spec.ts`

Spec §4.10 is the authoritative list. `SeriesChartView.vue`, `SeriesChartTile.vue`, `ChartControls.vue`, `DocumentSeriesTrend.vue` and the two charts composables all **stay** — they have a live consumer on the document detail page and are plan 5's to remove.

- [ ] **Step 1: Add a router spec that pins the ordering**

```ts
// encode_series_id produces `{sender}-{kind}-{currency}`; encode_authored_series_id
// produces `a-{id}`. Neither is ever a bare integer, so the two shapes coexist —
// but only if the digit-constrained route is declared FIRST.
it('routes a numeric id to the workspace and a series id to the old view', () => {
  expect(resolve('/charts/7').name).toBe('spending-workspace')
  expect(resolve('/charts/a-12').name).toBe('series-chart')
  expect(resolve('/charts/4-9-EUR').name).toBe('series-chart')
})
```

- [ ] **Step 2: Run it and watch it fail.** With `ChartsView` still mounted at `/charts` and no workspace route, `/charts/7` resolves to `series-chart`.

- [ ] **Step 3: Rewire**

```ts
{ path: '/charts', name: 'charts', component: () => import('../views/SpendingBoardView.vue') },
{ path: '/charts/:chartId(\\d+)', name: 'spending-workspace',
  component: () => import('../views/SpendingWorkspaceView.vue') },
{ path: '/charts/:seriesId', name: 'series-chart',
  component: () => import('../views/SeriesChartView.vue') },
```

The workspace route must be declared **before** `/charts/:seriesId`, exactly as
`/ask/new` already precedes `/ask/:threadId(\d+)`.

- [ ] **Step 4: Delete the old view and its spec.** Then `npm run type-check` to prove nothing else imported it.

- [ ] **Step 5: Commit** — `refactor(spending): /charts becomes the spending board`

---

### Task 12: E2E

**Files:**
- Delete: `frontend/e2e/charts.spec.ts`, `frontend/e2e/charts-layout.spec.ts`
- Create: `frontend/e2e/spending-board.spec.ts`, `frontend/e2e/spending-layout.spec.ts`
- Modify: `frontend/e2e/smart-groups.spec.ts`

`charts-layout.spec.ts` is the repo's reference geometry spec; its replacement
must be at least as strict. Both new specs use `requireStack()` and the
`fixtures/layout.ts` helpers.

- [ ] **Step 1: Repoint `smart-groups.spec.ts`**

It opens `/charts` through the sidebar and then asserts on the *old board's*
create flow. Its subject is authored series, not the board, so point its
`openChartsPage` at the single-series route it actually needs and drop the
`charts-create-*` interactions that belonged to the deleted view. Do not delete
the spec — its authored-series coverage is still the only one there is.

- [ ] **Step 2: Write `spending-board.spec.ts`**

The e2e database is fresh, so the empty state is the seeding path — which is
exactly §2.2's point, and means this spec exercises the ordinary save path:

```
sign in
  -> sidebar -> /charts
  -> empty state offers "All spending" first
  -> click it -> a card appears
  -> open the card -> /charts/{id}
  -> the toolbar, the chart, and all three footer blocks render
  -> overflow menu -> delete -> back to the empty state
```

**Do not assert a drill-through here.** The e2e database is fresh, so "All
spending" covers no documents: there are no bars to click and every footer group
is null, which makes a bucket-click step unreachable rather than failing — the
worst kind of test, one that cannot detect the breakage its name claims. E2E's
job on this branch is the route swap, the empty state's save path, the footer's
three blocks rendering, and the container geometry. Drill-through *content* is
unit-tested in Task 6 against fixtures that can express a merge, an amountless
document and a 422, none of which this archive can produce.

Assertions must hold on all three projects. Use `data-testid` throughout, and
`toBeVisible()` rather than `isVisible()`.

- [ ] **Step 3: Write `spending-layout.spec.ts` — and prove each guard reds**

Three geometry claims, measured rather than read off a class list:

```ts
test('the workspace toolbar is one row above the threshold and a chip below', …)
test('the drill panel sits beside the chart above the threshold', …)
test('the drill panel is docked to the bottom below the threshold', …)
```

Use `rectOf`, `expectDockedToBottom` and `expectNoHorizontalOverflow`. The
side-panel test skips on the two narrow projects with a named geometric reason,
as `charts-layout.spec.ts` already does for its single-row claim.

**Then prove the guards.** Swap `@3xl:` for `lg:` in the workspace and run the
spec: it must go RED, because a 1280px viewport with the sidebar expanded gives
a 1024px column while `lg:` reports 1280. Restore, and record the result in the
journal. A guard that has not been watched fail is not a guard.

- [ ] **Step 4: Run the full suite against the local stack**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-4b/frontend
npm run build && (npm run preview -- --port 4173 --strictPort &)
E2E_BASE_URL=http://localhost:4173 npm run test:e2e
```
Expected: green on chromium, mobile-webkit and tablet-webkit.

- [ ] **Step 5: Commit** — `test(spending): e2e for the board, the workspace and the container guards`

---

### Task 13: Documentation and the journal

**Files:**
- Modify: `docs/frontend.md` (§1.5 views and routes, plus its stamp)
- Create: `journal/260830-spending-view-board.md`

- [ ] **Step 1: Update `docs/frontend.md`**

Add the two views and the `components/spending/` set to §1.5, note that
`/charts` is now the spending board while `/charts/:seriesId` still serves the
document detail page's trend tile, and record the `@3xl` workspace threshold
beside the existing `@5xl` header one — including the measured table from Task
10, since the next person to touch it needs the numbers, not the conclusion.

Update the `**Last updated:**` and `**Last verified:**` lines at the top, naming
the method: geometry measured in the real stack via Playwright, palette computed
with the validator.

- [ ] **Step 2: Write the journal entry**

`journal/260830-spending-view-board.md`, H1 a clean title with no date or number
(`# The spending view's board`). Cover: the palette computation and why four is
a cap rather than a preference; why the entity-stable derivation in §4.4 was
replaced and what the arithmetic was; the container measurement and the guard
that was proved red; and anything the build found that the plan got wrong —
that last section is the one with the most value to the next plan.

- [ ] **Step 3: Regenerate the journal index and run the doc checks**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-4b
uv run python scripts/build_journal_index.py
make check-docs
```
Expected: both clean.

- [ ] **Step 4: Run everything, then commit**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-4b && make lint
cd /Users/john/projects/syncthing/agent-lxc/library-4b/frontend && \
  npm run lint && npm run type-check && npm run test:unit
```

```bash
git add docs/frontend.md journal/ docs/superpowers/
git commit -m "docs(spending): the board's documentation and journal entry"
```

---

## Definition of done

- `/charts` renders the board; `/charts/:id` the workspace; `/charts/:seriesId` still reaches `SeriesChartView` from the document detail page.
- All four frontend checks green: `npm run lint`, `npm run type-check`, `npm run test:unit`, `npm run test:e2e`.
- `make lint` green, and the backend suite untouched.
- E2E green on **all three** viewport projects.
- Every container-query guard demonstrated going red before being trusted, and the demonstration recorded in the journal.
- `docs/frontend.md` and the journal entry updated; `make check-docs` clean.
