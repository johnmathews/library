# The colour guard's gaps and the spending chart's missing month

> Purpose
>
> Records the work for issues #235 and #236: a raw-colour guard that missed whole forms of colour,
> and a spending chart that drew less than its design-system card described. It also records the
> owner's four rounds on the local demo stack and what was left for later. The living
> descriptions are [docs/brand.md](../docs/brand.md) §6.4 and
> [docs/spending-view.md](../docs/spending-view.md) §4.

**Date:** 2026-11-22
**Branch:** `eng-colour-guard-chart`

## 1. The guard (#235)

`no-raw-colour.spec.ts` caught Tailwind palette classes, `bg-white`/`text-black` and six-digit hex.
It missed `rgb()`/`rgba()` and the other colour functions, 3-, 4- and 8-digit hex, `ring-offset-*`,
`fill-white` and more. That was shown, not assumed: a planted file with those forms passed the old
guard (confirmed).

The tightened guard covers every colour function (case-insensitive, `color()` included), each hex
length, and a wider set of utilities (`ring-offset`, `fill`, `stroke`, `shadow`, `inset-ring`,
`drop-shadow`, `border-s/e` and others). One allow-list now covers all literals, with a reason per
file. On the tree as it stood it went red with 22 hits (confirmed). Those were moved onto roles:

1. The `.doc-markdown` rules were copied into four components' scoped styles with `rgb()` tints.
   They are now one block in `utility-patterns.css`: code, `pre` and table headers on `tile`,
   borders on `line`. Three views gained table styles they had lacked.
2. The thin scrollbar uses `quiet`, and `control-border` on hover. Its dark twins are gone.
3. `--shadow-sm` became an alias of `--shadow-card`.

The guard is green on the final tree. A planted file is red with all six forms named, and green once
it is removed (confirmed, run again at /done).

Code review at /done found that the comment stripper treated a `/*` inside a string
(`accept="image/*"`) as a comment start, which would hide what followed. A block comment now starts
only at the beginning, after whitespace or after a CSS delimiter. A self-test covers it.

## 2. The chart (#236)

The ChartStyle card described an empty-month stub and euro ticks that the chart did not draw. The
mockup `brand/design-system/mockups/261122-chart-gaps-and-colour-roles/` asked three questions. The
owner picked the recommended option on each: keep the month and draw a `quiet` stub, ticks as
"EUR 200", and the existing roles for markdown.

Why the month was missing: the API buckets cells with `GROUP BY`, so an empty period sends no cell,
and the axis was built from the cells (confirmed by reading `SpendingChart.vue` and the query). The
fix stays in the frontend. `spending/periods.ts` `fillPeriods()` fills every period between the
first and the last at the chart's grain, and a Chart.js plugin draws the stub. The stub has no
tooltip, no click and no band. `spending/money.ts` `formatTick()` writes the ticks with a true minus.

## 3. The owner's rounds on the demo stack

The owner looked at a local Compose stack seeded with invented data. It ran under its own project
name and its volumes were never reset.

1. "The bars are too skinny": the fixed 24px cap became about half the slot, at most 64px.
2. Thicker when there is room, "as long as they can get thin enough to fit". The bars are now about
   two thirds of the slot, at most 120px. The tooltip lists every band in the hovered bar and the
   bar's total. "One order everywhere": the owner picked the largest band on the baseline, Other on
   top, the legend largest first, and the tooltip top segment first. Colours are still assigned by
   value before the sort, so a band keeps its colour when the order changes (tested).
3. The tooltip and axis text were "very small". The design system already set a floor, `type-small`
   (12.5px), but its Charts section said 11px, and the tooltip used Chart.js's default 12px
   Helvetica. The ticks are now 12.5px and the tooltip 14px, both in `--font-sans`. The system's
   Charts section and the card now agree with its floor. No mockup round was needed, because this
   applies a rule the system already had.

Code review at /done also found that the stub was 4px wider than the bars' colour. Chart.js paints
the 2px surface-coloured segment gap inside each bar, so the stub is now drawn at the colour's width.

## 4. Checks run

Confirmed, each on the final tree unless noted:

1. Frontend: 1597 Vitest tests, lint, type-check, build and the asset check.
2. Backend: 2547 passed with 95% coverage. This run predates the last frontend-only edits.
3. ruff, mypy, actionlint, the design-system gate `--complete`, and `check_docs`.
4. The three e2e shards on a separate local stack: desktop 66 passed (1 flaky,
   `ask-page-citation.spec.ts`, passed on retry), mobile 48, tablet 44. This run predates the
   stub-width fix and the guard's self-test additions, neither of which an e2e spec reaches.

## 5. What is deliberately not done

1. Empty periods before the first period with money or after the last are not filled. The owner
   picked "first to last". Filling to the window's `since`/`until` is #241.
2. Named colours (`color: white`, `fillStyle = 'black'`) are not caught by the guard. None are in the
   tree; #242.
3. The dark shadow roles are `none`, which would void a ring on the same element. No element
   combines the two; #239.
4. The design system's Cover card overflows at 375px. That was already true on main; #240.
5. The phone chart's x-axis dates (`2026-06-01`) tilt because they are long. That predates this
   work and is left alone.
6. `formatMoney` elsewhere still writes a hyphen-minus; #238.
