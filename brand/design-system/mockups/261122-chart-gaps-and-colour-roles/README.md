# Empty months, axis money and the last raw colours

**Question:** three small picks behind issues #236 and #235. How the spending chart shows a
month with no documents, how its y-axis writes money, and which roles replace the last `rgb()`
colours (markdown code and tables, the thin scrollbar). The rest of the chart and the notes stay
as they are.

**Status:** decided (2026-11-22)

**Link:** https://claude.ai/artifact/B3TGBBLmbC3sPVvdh22C9r

## Options

### 1. A month with no documents

Today the axis is built from the months that have documents, so an empty month is not on it.

- **A. Keep the month, draw the stub.** Every period from first to last on the axis; a 3px
  `quiet` stub where there is nothing. The card stays as written. Recommended: the gap shows
  and reads as empty. `quiet` on `panel` is 2.5:1 in light (3.1:1 dark); the month label is the
  other signal.
- **B. Keep the month, leave it empty.** The card drops the stub.
- **C. As today.** The month is not on the axis; the card drops the stub.

### 2. Money on the axis

Every other figure in the app reads "EUR 991.00" (`spending/money.ts` avoids symbols on purpose).
The card's own totals and legend use €, so they change to match the app whatever is picked.

- **A. The currency code: "EUR 200", "−EUR 100".** One money format on the card. Recommended.
- **B. The symbol: "€200", "−€100".** The card as written; other currencies fall back to the code.
- **C. Bare numbers, as today.** The card drops the euro ticks.

### 3. Markdown code, tables and the thin scrollbar

- **A. Existing roles.** `tile` for code and header fills, `line` for table borders, `quiet` for
  the scrollbar thumb. No new token. Recommended.
- **B. Today's look, mixed from `ink`.** `color-mix(in srgb, var(--ink) 7%, transparent)` and
  similar; the guard allows `color-mix()` of a role.

## Decision

**A, A, A**, 2026-11-22, the recommended option on each question. The owner's answers:
"A. Month + quiet stub (Recommended)", "A. EUR 200 (Recommended)", "A. Existing roles
(Recommended)". Recorded in `docs/brand.md` §4. Built in the PR that closes #235 and #236.

## Rounds

- **1, 2026-11-22:** three questions, each option in a spending card or a note in both themes.
  Figures are made up.
- **2, 2026-11-22:** on the local demo stack the owner said the bars were too skinny. The chart's fixed
  24px cap became about half of each slot, at most 64px, as this card's preview already draws them.
- **3, 2026-11-22:** bars thicker when there is room (about two thirds of a slot, at most 120px); the
  tooltip shows the whole bar; one order everywhere. The owner picked "B. Tooltip mirrors the bar":
  largest band on the baseline, legend largest first, tooltip top segment first, then the total.
- **4, 2026-11-22:** the owner found the tooltip and axis text too small. The chart was below the
  system's own floor (`type-small`, 12.5px): ticks at 11px, the tooltip at Chart.js's 12px
  Helvetica. Ticks became 12.5px and the tooltip `type-body` (14px), both in `--font-sans`. This
  restores the system's rule, so no new mockup.
