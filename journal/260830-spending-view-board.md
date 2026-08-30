# The spending view's board

**Date:** 2026-08-30
**Branch:** `spending-view-board`

## 1. What shipped

Plan 4b of the spending-view work, thirteen implementation tasks on top of the
chart engine (`b32a67c`) and its backend surface (plan 4a, `60d6c95`): the
`/charts` **board** (`SpendingBoardView.vue`, one card per saved chart,
reorderable by keyboard and drag) and the `/charts/:chartId` **workspace**
(`SpendingWorkspaceView.vue`, the full toolbar/chart/legend/footer/drill-panel
view for one chart). Built from a new `components/spending/` set — the
stacked-bar chart, the legend, the footer accounting statement, the board
card, the drill-through panel and its three bodies, the free-text question
draft, the empty state — on top of two pure modules, `spending/money.ts`
(exact decimal-string arithmetic) and `spending/palette.ts` (the fold and
band-colour assignment every chart component reads from). `ChartsView.vue`,
the pre-board grid, moves to `/charts/legacy`. Full details, including every
route/testid, are in `docs/frontend.md` §1.5.

## 2. The palette: six slots, shared, and validated on this app's own surfaces

The plan's own Task 2 computed a **four**-slot palette and had it passing its
own contrast checks — 7.7 in dark mode, inside the accepted 6–8 floor band,
so not wrong, just not much margin. Partway through the branch, a parallel
session (plan 4c, building the split-vocabulary picker) published a
**six**-slot module, `@/utils/splitPalette.ts`, and asked that this plan not
ship a second one: both plans derive a colour from a split value's hash, and
a chart-axis colour and a picker-swatch colour disagreeing on the same value
would be a visible defect neither plan alone could see.

The six-slot set was not adopted on trust. It was re-validated independently
against **this app's own chart surfaces** — the light card background and the
dark `gray-800` card — over the all-pairs pairlist in both themes: CVD ΔE 9.9
light / 9.3 dark (target 8), normal-vision 19.8 / 17.2 (floor 15). Strictly
better than the four-slot set on every number. "All-pairs" mattered because a
**hash-derived** slot assignment has no ordering to lean on — unlike a
palette handed out in a fixed sequence (slot 0, then 1, then 2, ...), where
checking adjacent pairs is enough because the walk never revisits a
combination, a hash can put any two colours next to each other in any chart,
so every pair has to clear the floor, not just the ones a sequential fold
happens to produce.

Adopting the shared module also reopened a design call the user had made
under a premise that had just changed: the user chose **key-order** slot
assignment (the surviving bands are sorted by their value key and assigned
slots in that order — deterministic regardless of arrival order, not "the
first one seen gets slot 0") on the premise of a four-slot palette. Re-measured
with **adversarial** keys (not a fixture whose
keys shared a formulaic prefix, which had produced a misleadingly high first
reading of 99.6%), a band keeps its hash-derived slot across a reorder only
92.5% / 76.5% / 60.7% of the time at 2 / 4 / 6 bands — key-order has that
stability property by construction, hash-derived doesn't. The ruling was to
keep the spec's original hash-derived-with-de-collision wording (one
definition shared with 4c's panel, six slots beat four in the floor band, and
de-collision preserves every property the user actually cared about — no
same-colour bands, deterministic under reordering, no repaint on filter) and
flag the tradeoff to the user rather than deciding it unilaterally. This
branch's own copy of `splitPalette.ts` (identical contract and hexes, so the
two branches' tests run independently) is a duplicate to delete at merge,
whichever branch lands second — see `prefer-removing-the-second-copy` in
project memory for why that's the right shape of fix rather than a
comparison test between the two copies.

## 3. The container work: one measurement, two mechanisms, both guards proved red

The workspace toolbar and the drill panel both need to know whether the
content column is wide enough for a full row, and neither can trust the
viewport to answer that: the column is the viewport minus a sidebar the user
collapses independently, so the same 1280px viewport is a 960px column with
the sidebar expanded and a 1136px column with it collapsed. Measured directly
in the running stack via Playwright, not read off a class list:

| viewport | sidebar | `#app-page` border box | padding | content box |
| --- | --- | --- | --- | --- |
| 1280 | expanded | 1024 | 32 | **960** |
| 1280 | collapsed | 1200 | 32 | **1136** |
| 656 | overlay | 656 | 24 | **608** |
| 375 | overlay | 375 | 16 | **343** |

A container query evaluates against the **content box**, so the padding
comes off — reading the border box gets the 1280-expanded case backwards
against the header's own `@5xl` (1024px) threshold: the border box is
*exactly* 1024, which reads as "at the threshold, should merge", but the
960px content box the query actually sees is below it, and the header
correctly stacks.

That measurement alone would have been enough for the toolbar, which is an
ordinary in-flow element and takes a **named** container query
(`@container/workspace` + `@3xl/workspace:`) — named because `PageHeader`
already opens its own **unnamed** `@container` around the `#controls` slot,
and an unnamed query inside the workspace would silently have bound to
PageHeader's container instead of the workspace root. Caught before shipping,
by the implementer, not by review.

The drill panel is a different problem the same measurement can't solve. It's
a native `<dialog>` opened with `showModal()`, which puts it in the browser's
**top layer** — not a descendant of any container in the document, so no
`@container` rule reaches it and no custom property inherits into it. The
plan's own text had assumed the dialog could query a container; it can't.
The workspace instead runs a `ResizeObserver` on its own content column and
hands the panel a resolved `sheet` boolean as a prop — one shared threshold
constant (`SHEET_THRESHOLD_PX`, the same 768px the toolbar's `@3xl/workspace`
class is keyed to), computed once, in two different mechanisms because the
DOM gives no single mechanism that reaches both.

Neither guard was trusted on the strength of having been written. Both were
watched failing: swapping the workspace's `@3xl:` for `lg:` and running
`e2e/spending-layout.spec.ts` reds at a 1024px viewport, sidebar expanded —
"column is 704px (<768px) — the chip must show" resolves to hidden instead,
because `lg:` (a 1024px viewport breakpoint) has no way to know the column is
narrower than the viewport. The pre-existing `@5xl` → `lg:` swap on
`e2e/header-toolbar.spec.ts` reds the same way at the header's own threshold:
expected `false` (stacked), received `true` (merged), at 1280px with the
sidebar expanded.

## 4. The first-run bug: the flagship proposal 422'd on a fresh archive

The empty state's first, pinned proposal — "All spending", an unrestricted
rule split by `category` — is meant to be the one thing a brand-new install
can always click. It wasn't. `default_split: 'category'` only works once the
`category` facet vocabulary has been seeded, and seeding is an **operator**
step (`library label-archive`), never automatic on migrate or startup. A
genuinely fresh archive has no facet vocabulary at all, so
`POST /api/spending` rejected the split axis with a 422 — the single most
important action on the first-run screen was broken for every new install,
and no unit test caught it, because every fixture for that component
supplied at least one facet.

It surfaced during Task 12's e2e run, which uses the CI e2e database in its
actual fresh state rather than a seeded one. The fix keys the proposal on
whether `GET /api/facets/counts` returned **any** rows at all — some means
the vocabulary is populated enough to split by `category` (today's
behaviour); none means propose the total **unsplit** instead, which a fresh
archive can always draw — rather than probing for the `category` facet by
name, which would still be broken for an archive whose vocabulary exists but
happens to have no `category` values yet.

The find is only as good as the environment that produced it, and this is
where the CI e2e job's deliberately-unseeded database earns its keep twice
over. Seeding it to make this bug's own reproduction case go away would have
hidden the bug itself — the fresh-archive path would simply never have been
exercised again — and, separately, Task 12 found that a facets.spec tablet
failure earlier in the day was caused by exactly that seeded vocabulary
leaking in from a concurrent subagent's run, not by this branch: keeping the
database unseeded is what let both findings resolve correctly instead of
one masking the other.

## 5. What the plan got wrong

This is the section with the most value to the next plan, so it's recorded
plainly rather than folded into the task list above.

- **A reorder assertion pinned the wrong contract.** The plan's Task 9 brief
  specified the PATCH calls for "move the card at index 0 down" as
  `[[1, 0], [2, 1]]`. The arithmetic is backwards: moving index 0 down swaps
  it with index 1, so the card that *was* id 2 takes ordinal 0 and the card
  that *was* id 1 takes ordinal 1 — `[[2, 0], [1, 1]]`. Caught by running it,
  not by re-deriving it on paper a second time, which is exactly the failure
  mode: reasoning about arithmetic doesn't catch reasoning errors in
  arithmetic. Fixed to assert order-independently, since the two PATCHes
  carry no ordering requirement of their own.
- **A claimed mechanism that wasn't one.** The plan asserted that vue-router
  route **declaration order** was load-bearing for `/charts/:chartId(\d+)`,
  `/charts/legacy`, and `/charts/:seriesId` to coexist without one route
  swallowing another. It's false for vue-router 5.1.0: the router resolves by
  **segment specificity** (static segment beats a regex-constrained param
  beats a bare param), not declaration order — verified by reordering the
  routes to the worst case (the bare `:seriesId` route first) and confirming
  all four resolutions still hold. Order is kept as defensive convention, and
  the code comments now say the true mechanism instead of the false one.
- **An instruction to delete a view that held the only UI for a live
  feature.** The spec's own §4.10 said to delete `ChartsView.vue` once the
  board replaced it. It couldn't be — the Smart Groups **creation** UI (the
  create form, the document search-to-add, the staged-review backfill modal)
  exists nowhere else, and deleting the view would have orphaned a live
  backend with no client. This had to be escalated to the user rather than
  resolved by the plan's own authority: the view now survives at
  `/charts/legacy`, unlinked from the sidebar, until a later plan deletes it
  together with the series backend it serves.
- **A fourth e2e spec the removal list missed.** The plan's enumeration of
  which specs drive the pre-board grid listed three. There were four:
  `header-toolbar.spec.ts` — the repo's reference geometry guard for
  `PageHeader`'s container-query merge — also drove the old board and had to
  be repointed to `/charts/legacy`, where `PageHeader` and `ChartControls`
  still coexist. A partial enumeration, like a partial grep (below), reads as
  complete right up until something built on top of the missing item breaks.
- **Three rulings that never reached the task obliged to implement them.**
  A currency-picker ruling made in Task 8's brief needed to land in Task 9;
  a refetch-opacity requirement and a legend "reset" requirement both needed
  to land in Task 10. All three were satisfied on the letter of their own
  task's review — because the obligation lived in a *different* task's brief
  — and each had to be caught and fixed after the fact. The mitigation
  applied for the rest of the branch: before dispatching a task, grep its
  brief for every ruling that names it, not just the ones written into its
  own numbered section.

One near-miss from the same family, worth naming even though it was caught
inside a single task rather than crossing one: fixing the back-link
regression (`SeriesChartView.vue`'s post-delete redirect, which used to
correctly return to the series grid and after the swap silently landed on
the spending board instead) was declared clean on the strength of grepping
for `router.push('/charts')` — which structurally cannot match a
`RouterLink to="/charts"`. The second, identical regression on the page's
own "← All charts" link survived that first pass entirely. A partial grep
presented as a clean sweep is how the second instance survives the first
fix; the round 2 fix produced a *complete* enumeration of every `/charts`
literal in the affected components, in any syntactic form, specifically so
nobody downstream had to trust another partial one.

## 6. Tests that could not fail

Four tests were found not just wrong but structurally incapable of catching
the defect they were written to catch, and were strengthened rather than
left as decoration:

- **A money-exactness test whose fixtures were all exact.** The test asserting
  `toCents` "stays exact where floats do not" used `0.10`, `0.20`, `1284.50`,
  `1142.20` — and `Number(s) * 100` happens to land on an exact integer for
  all four, so the naive-multiply mutation the test exists to catch could not
  turn it red. Verified in node before trusting the fix: `0.29 * 100` is
  `28.999999999999996`, `0.57 * 100` is `56.99999999999999` — a lost cent
  either way. Adding those two values made the mutation check actually red.
- **A summation test with one payment.** The drill panel's "payments sum to
  the bar total" test used a fixture with exactly one payment, so the
  render's summation loop was never exercised with more than one item and a
  summation defect had nothing to disagree with. Replaced with an
  adversarial multi-payment fixture carrying a largest-remainder rounding
  case (three thirds of a total that isn't evenly divisible by three, so
  naive equal-thirds division — `33.33 × 3 = 99.99` — visibly disagrees with
  the real total).
- **A scale-type test asserting only labels.** The chart's x-axis labels come
  from the chart's periods regardless of whether the axis is configured as a
  `CategoryScale` or a `TimeScale` — so a test asserting only the rendered
  labels could not detect a scale-type regression, the exact defect class
  this component exists to avoid (stacked bars need `CategoryScale`; the
  pre-existing `SeriesChartTile` next to it uses `TimeScale`, registered
  globally and additively, so a silent swap is easy to introduce). Fixed by
  asserting `options.scales.x.type === 'category'` directly.
- **An assertion on a page heading that both pages render.** `ChartsView`
  and `SpendingBoardView` both title themselves "Charts" via `PageHeader`.
  `smart-groups.spec.ts`'s landing assertion,
  `getByRole('heading', { name: 'Charts', exact: true })`, would have passed
  whether the spec actually landed on the legacy view or, on a routing
  regression, the new board — it could not tell the two pages apart. Fixed
  to assert a page-unique testid (`charts-create-button`, present only on
  the legacy view) instead of the shared heading text.

## 7. Verification

`e2e/spending-board.spec.ts` and `e2e/spending-layout.spec.ts` (new),
`e2e/header-toolbar.spec.ts` and `smart-groups.spec.ts` (repointed) all green
on chromium/mobile-webkit/tablet-webkit against a **fresh** e2e database: 134
passed, 0 failed, 36 skipped. The full spending-adjacent regression sweep —
router spec plus every `/charts`-touching unit spec — is 180/180 across 15
files after the Task 11 back-link fix; the last full-repo frontend run before
the route swap was 1364/1364 across 109 files, coverage above the gate on
every changed file. `npm run type-check` and `npm run lint` clean throughout.
`uv run python scripts/build_journal_index.py` and `make check-docs` both run
clean for this entry.
