# Dashboard: Rows by default

> Purpose
>
> Records how the dashboard layouts trial ended: Rows became the default on every device, the
> other three layouts stayed, and the table stopped being able to flip to the list and back. The
> living description is [docs/frontend.md](../docs/frontend.md) §1.2, "Dashboard layouts"; the
> trial itself is in [261122-dashboard-layouts-trial.md](261122-dashboard-layouts-trial.md).

**Date:** 2026-11-27
**Branch:** `eng-dashboard-layout-default`
**Issues:** #246, #247

## 1. The pick

The owner had not tried the layouts on real documents yet, so the choice was made on a mockup:
the four layouts on invented documents at 1440px and 375px in both themes, plus a full-size page
for a phone (`brand/design-system/mockups/261127-dashboard-layouts/`). The mockup recommended
Table, which shows as the list on a phone. The owner chose **Rows for the phone and Rows for the
desktop and tablet**.

Two more answers shaped the change:

1. **Keep every layout.** Tiles, Table and List stay in the Layout menu; only the default changes.
2. **Keep the tile settings.** "Tiles per row" and "tile preview" still change how Tiles look, so
   they stay, and the backend is untouched.

## 2. Decisions

1. **A new key, `library:doc-layout-v2`.** `useStorage` writes its default on first read, so the
   trial's key holds `tiles` on every browser that opened the dashboard, chosen or not. Changing
   the default in place would have changed nothing on the owner's own devices. A device where
   someone picked Table or List during the trial starts on Rows once.
2. **Rows first in the Layout menu**, then Tiles, Table and List.
3. **#247 by hysteresis, not `scrollbar-gutter`.** The list turns back into the table only 24px
   past `tableMinWidth` (`TABLE_WIDTH_MARGIN`, `nextIsWide`); inside that band the last answer
   stands. The issue suggested 16px, but a Windows scrollbar is 17px, which leaves a band where
   it still flips; a unit test fails for any margin of 17px or less. `scrollbar-gutter: stable`
   on `#app-content` would also work, but it reserves a gutter on every page for anyone with
   classic scrollbars, to fix one view.
4. **Tests choose their layout.** The view spec and the e2e specs about tiles (the grid, the top
   band, tiles per row) pin Tiles before the page loads (`pinDashboardLayout` in
   `e2e/fixtures/layout.ts`). Specs that only open a document use the `doc-card` hook every layout
   keeps, instead of the tile-only `.app-doc-card__title`.

## 3. What the browser showed about #247

The flip had never been observed. A script on the local stack reproduced it in Chromium with a
17px classic scrollbar forced on `#app-content`. One gotcha on the way: Playwright launches
headless Chromium with `--hide-scrollbars`, so a scrollbar measures 0px until that default
argument is dropped.

1. **With the default six fields, the table's page and the list's page are the same height to
   within 1px** (800px and 801px for six documents, the list being the taller). The loop needs
   the table's page to be the taller one, so in practice it is very unlikely.
2. **With the table made 60px taller to force it,** the old rule flipped 27 and 28 times a second
   with the results area 5px and 12px past the minimum. The new rule switched at most once and
   held. Neither logged a ResizeObserver warning.

## 4. Also changed

The Design System's DocumentTile card now shows the row as the dashboard's default beside the
tile, and DataTable names the dashboard table and its list fallback. The mockup records the
decision.
