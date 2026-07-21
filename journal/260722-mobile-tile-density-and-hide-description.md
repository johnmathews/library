# Mobile tile density and hide-description option

**Date:** 2026-07-22
**Branch:** `ux/mobile-tile-density`

## What & why

The mobile dashboard felt cramped and wasteful: tiles sat a full `1rem` from
the screen edges with a `0.75rem` gap between them, and each tile was long and
skinny — dominated by a multi-line description. Two changes:

1. **Tighter mobile layout (unconditional, phones `<= 640px`).** Reduced the
   inter-tile gap `0.75rem → 0.375rem`, and broke the grid out of the page's
   `px-4` gutter with `margin-inline: -0.75rem` so tiles sit ~`0.25rem` from the
   screen edge. Scoped to `#dashboard-grid` so the page header and filter bar
   keep their normal gutter — only the tile grid goes near-edge.

2. **"Hide tile description on mobile" — a synced account setting.** New
   `hide_summary_mobile` preference (default off) that adds
   `.app-doc-grid--hide-summary-mobile` to the grid; CSS hides each tile's
   `.app-doc-card__summary` at `<= 640px`. The search snippet is left in place
   (query context, not the always-on description).

## Decisions

- **Server-synced, not per-device.** Followed the existing `phone_columns`
  precedent exactly — same appearance-preferences path, same mobile-only
  intent — so the setting syncs across devices. (User confirmed.)
- **CSS class hook, not a `v-if`.** The element stays in the DOM; a `<= 640px`
  media rule hides it. No reactive media-query churn, and desktop rendering is
  provably untouched.
- **Grid breakout for the gutter, not a global padding cut.** Reducing
  `#app-page`'s mobile padding would have touched every view (forms, detail,
  etc.). The negative-margin breakout keeps the blast radius on the dashboard.

## Surfaces touched

`hide_summary_mobile` mirrors `phone_columns` end-to-end: `schemas.py`
(resolver + `AppearancePreferences`/`UserPreferences` + validator),
`api/settings.py` (`put_appearance`), `api/settings.ts` (`updateAppearance`),
`stores/auth.ts` (`hideSummaryMobile`), `SettingsView.vue` (Appearance toggle),
`DocumentListView.vue` (grid class), `utility-patterns.css` (gap + breakout +
hide rule). Tests added on all layers; docs updated (`frontend.md`, `api.md`).

## Verification

Full backend suite (1318 passed), full frontend unit suite (1034 passed),
whole-repo ruff check + format, `vue-tsc`, eslint, and a production `vite build`
all green. Live on-device mobile visual pass not run here (needs the full stack
+ seeded data) — the CSS is declarative and the class hook is unit-tested.
