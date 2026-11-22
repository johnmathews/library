# Dashboard layouts trial

> Purpose
>
> Records why the Documents dashboard gained Rows, Table and List layouts beside the preview tiles,
> what was decided while building them, and what happens once the owner picks one. The living
> description is [docs/frontend.md](../docs/frontend.md) §1.2, "Dashboard layouts".

**Date:** 2026-11-22
**Branch:** `eng-dashboard-layouts`

## 1. Why

The owner's verdict on the preview tiles: they still work, but they "dont help me stay organised".
A 4:3 page image takes most of a tile's height, so a screen holds few documents, and the images
look alike. The owner was unsure which replacement would help, a column of wide thin tiles, a
table or a list, so all three are built as a trial and chosen per device with a Layout select.
Tiles stay the default, and nothing is removed until a winner is picked.

## 2. Decisions

1. **One copy of the fields.** The tile's meta row moved into `DocumentFields.vue` and
   `DocumentFieldValue.vue` before any layout was added. All 81 existing view tests passed
   unchanged after the move (confirmed). Breaking the amount rendering on purpose went red only
   in the new spec, so a tile's amount had no test before this (confirmed).
2. **Per device, in localStorage** (`library:doc-layout`). The right layout depends on the screen,
   so it is not an account setting.
3. **Rows show title and metadata only**, as the owner described them. No summary line. The search
   snippet still shows while searching, since it says why a document matched.
4. **The table falls back to the list on a measured width.** The results area, not the viewport,
   decides, and the threshold grows with the number of field columns
   (`tableMinWidth`: 240px plus 112px a field, at least 640px).
5. **Every layout keeps the `doc-card` hook**, so the seven e2e specs that open documents through
   it keep working whichever layout becomes the default.

## 3. What the browser showed

Each layout was walked on a local stack with 30 synthetic documents, at 375 to 1440px with the
sidebar open and closed.

- The evaluation's F4 held (confirmed). At 1024px with the sidebar open the results area is 704px,
  narrower than at 768px (720px), where the sidebar overlays. A viewport breakpoint would have
  picked the wrong layout.
- A fixed 640px threshold was wrong too (confirmed): with six field columns the table scrolled
  sideways inside its card anywhere from 640 to about 910px. The field-count threshold replaced
  it, and the remeasured table never scrolled where it showed.
- Rows first put the fields beside the title, which cut titles short. Rows now stack the title over
  the fields. The table and list were first wrapping to two to four lines a document. Truncating
  titles and tag lists, and short dates in the list, made both one line a document (25 of 25 list
  lines measured one line at 1280px).

## 4. What the wrap-up review changed

A code review before the push found two things, both fixed with a test that went red without the
fix (confirmed):

- A Cmd/Ctrl/Shift/Alt-click on a table row's cells opened the document in the same tab. The row
  click now ignores modified clicks, as `DownloadLink.vue` already does.
- The results area unmounts while a new filter loads, and `useElementSize` reads 0 for the
  remounted element until the observer reports. Since 0 counts as wide, a narrow area chose the
  table first on every filter change. The view now keeps the last real measurement. Whether the
  table was ever visible for a frame is not known: the observer normally reports before paint
  (suspected, not observed).

## 5. What is deliberately not done

- **No hysteresis at the threshold.** The review suspected the table and list could alternate on
  desktops with classic scrollbars, when one layout scrolls and the other does not. It was not
  seen in the browser, the owner's devices use overlay scrollbars, and a fix would add state for a
  narrow case. Filed as #247.
- **No layout removed, and tiles stay the default.** That waits on the owner's pick (#246).
- **No Design System artifact update.** The artifact describes tiles; it changes once a winner is
  known (#246).

## 6. Next

The owner uses the layouts on real documents and picks one (#246). Then, as a separate change:
make it the default under a new storage key (a default change needs one, view principles §4),
decide whether to remove the other layouts and the tile-only preferences, and update the Design
System artifact's DocumentTile and DataTable cards.
