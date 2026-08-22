# One header toolbar instead of two bands

> `/charts`, `/jobs` and `/matters` each opened a second full-width band below
> `PageHeader` purely to hold a filter bar, while the header row beside it sat
> mostly empty. `PageHeader` now takes a `#controls` slot and renders one
> toolbar — controls left, page commands right.

## 1. What prompted it

A screenshot of `/charts` on a wide desktop: two stacked bands, the upper one
holding two right-aligned buttons and nothing else, the lower one holding four
filter fields and nothing else. The same shape turned out to be on `/jobs`, and
on `/matters` the second band held a **single checkbox**.

It is the same waste PR #90 went after from the other direction (the page title
moved into the app bar) and the same waste `PageHeader` already avoids when it
renders nothing at all for a bare title.

## 2. The part that needed measuring

The obvious objection is width: the controls have intrinsic widths that do not
shrink, so a merged row only pays off if it actually fits. That is a geometry
claim, so it got measured against the real stack rather than argued.

Measured on `/charts`, worst-case actions (both buttons present):

| viewport | sidebar | `#app-page` | merged? |
|---|---|---|---|
| 1152 | expanded | 896 | no |
| 1280 | expanded | 1024 | no |
| 1360 | expanded | 1104 | yes |
| 1152 | collapsed | 1072 | no |
| **1280** | **collapsed** | **1200** | **yes** |
| 1440 | collapsed | 1360 | yes |

The controls need 623px and the actions 348px, so the row wants ~987px of
content column.

The two bolded-adjacent rows are the finding: **at the same 1280px viewport the
answer differs depending on whether the sidebar is collapsed.** The content
column is the viewport minus a sidebar the user toggles independently, so a
`lg:` breakpoint is wrong in both directions — it would stack a 1200px column
with room to spare, or merge a 1024px one without. The merge is therefore a
**container** query (`@5xl`, 64rem of container width, a deliberate ~37px above
the measured 987px need).

This is the second time this repo has been bitten by treating a nested column's
width as if it were the viewport's; the first was the `/ask` panes.

## 3. What changed

- **`PageHeader`** gains `#controls`. With the slot present the row switches
  from `items-center` to `items-end` (labelled fields align on the inputs'
  bottom edge, and the buttons join it) and the actions are pushed right by
  `@5xl:ml-auto`. Below the threshold both groups go full width and stack,
  reproducing the old layout exactly — DOM order is visual order at both widths,
  so focus order never disagrees with the screen. Without the slot the row is
  byte-for-byte the old lede/actions row, so the other seven views are untouched.
- **`/charts`, `/jobs`, `/matters`** pass their existing bars through the slot.
  `ChartControls` itself was not touched, so `/series/:id` — which uses the same
  component next to its export buttons — is unaffected.
- **`/jobs`' filter bar** was rebuilt from `AppSelect`/`AppInput` to raw
  `.form-*` + `.filter-label`. Those components carry the *stacked-form* label,
  and the bar now shares a row with the header's own controls, so the two label
  recipes would have sat side by side — the exact mismatch
  `frontend-view-principles.md` §5 exists to prevent. Its document field's hint
  became a placeholder: a hint line under one field breaks the row's shared
  bottom edge.
- While there, the jobs document field dropped `v-model` for an explicit
  `:value` + handler. `v-model` and a template `@input` both fire on the same
  event and `searchDocuments()` reads `documentInput.value`, so their ordering
  decided whether it searched this keystroke or the last one.

## 4. Verification

`e2e/header-toolbar.spec.ts` (5 tests) asserts the merge, the right-push, the
stacked case, the jobs label recipe, and — the important one — the 1280px
container-vs-viewport split. That last test was confirmed to go **red** by
swapping `@5xl:` for `lg:` and re-running, so it genuinely guards the choice
rather than restating it.

Frontend unit suite: 1102 passing (three new `PageHeader` cases). The one
pre-existing failure was a spec asserting `flex`/`justify-between` on the header
*root*, which is now the `@container` wrapper — the assertion moved to the row
and the layout claim itself moved to the geometry spec, where it belongs.

## 5. Found during wrap-up

Two things the first pass missed, both caught by auditing rather than by
re-reading what I had written:

1. **Stale doc claims the first doc pass didn't reach.** Updating the
   `PageHeader` section of `docs/frontend.md` felt like "the docs are done", but
   the per-view table further down still described `/jobs`' task filter as an
   `AppSelect` — which the same commit had just replaced. Also missing: the new
   `header-toolbar.spec.ts` in the layout-spec enumeration, and any mention of
   the matters archived toggle. The lesson is the boring one: derive the changed
   surfaces from the diff and check each, because the surface you *edited* is
   the one you stop looking at.
2. **A silent slot drop in the new primitive.** `PageHeader` rendered its named
   `#description` slot only in the no-controls branch, so a view passing both
   `#description` and `#controls` would have lost the description with no error
   — and `hasBody` counts that slot, so the header would have rendered *because*
   of content that never appeared. No view passes both today, so nothing was
   broken; it was a trap laid for the next one. Slotted content now takes its own
   line above the toolbar, exactly as the `description` prop does, with a
   regression test confirmed to go red without the fix.

## 6. What is deliberately not done

- **No other view was converted.** `/documents` keeps its `DocumentFilterBar` as
  a separate band. It is a much larger bar (a full multi-field filter surface,
  not a four-control strip) and would not fit a header row at any realistic
  width, so merging it would only produce a permanently-wrapped toolbar. The
  slot is available if that bar is ever slimmed.
- **`ChartControls` was not given a compact variant.** The measured need
  (987px) fits comfortably above the threshold, so shrinking the controls would
  buy a lower breakpoint nobody asked for.
- **The threshold is one number, not per-view.** `/jobs` and `/matters` need far
  less width than `/charts` and could merge earlier, but a per-view threshold
  would make the layout rule un-memorable for the sake of a band that only
  appears in a narrow window of widths.
- **No visual-regression snapshots.** The geometry spec asserts relationships
  (merged/stacked, left/right, no overflow) rather than pixels, which is what
  survives a font or padding change. Pixel snapshots for these three headers
  would fail on every unrelated restyle.
