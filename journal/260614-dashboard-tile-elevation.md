# Dashboard tile elevation

**Date:** 2026-06-14

## 1.1 Problem

The documents dashboard rendered each tile as a `bg-white` card with
`shadow-xs` (a near-invisible `0 1px 1px rgb(0 0 0 / 0.05)` shadow) and a
`border-gray-200` border, sitting on the `gray-100` (`#f3f4f6`) page. White on
gray-100 with that shadow gave almost no separation, so tiles read as flat,
borderless rectangles blending into the background.

## 1.2 Change

Chosen direction (with the user): **elevated cards**, scoped to tile contrast +
spacing polish — no IA or layout changes, cohesive with the existing Mosaic
violet/gray language.

- `frontend/src/assets/utility-patterns.css` — `.app-doc-card` now owns the
  elevation: `border-radius: 1rem`, a layered resting shadow
  (`0 1px 2px / 0.06` + `0 4px 16px -2px / 0.08`), and a hover state that lifts
  the tile 3px, deepens the shadow, and warms the border to `violet-200`. Dark
  mode (`.dark .app-doc-card`) drops the light shadow — invisible on a near-black
  page — and leans on the gray-800-on-gray-900 surface + border, deepening the
  shadow and accenting the border to `violet-500` on hover. A
  `prefers-reduced-motion` guard removes the lift. Added `.app-doc-card__title a`
  `-webkit-line-clamp: 2` so long titles keep tiles a consistent height.
- `frontend/src/views/DocumentListView.vue` — dropped the now-redundant inline
  `shadow-xs rounded-xl hover:shadow-md transition` (CSS owns these), added a
  `border-b` divider under the thumbnail, bumped body padding `p-4 → p-5`, and
  the title weight `font-medium → font-semibold` (kept `text-violet-600`, a test
  contract).

## 1.3 Verification

Design was iterated against a faithful standalone HTML reproduction of the card
styles (same hex/shadow/rem values), screenshotted in light + dark — before/after
confirmed the tiles now clearly lift off the page. Then in the real app:
`npm run lint`, `npm run type-check`, full `vitest run` (170/170), and
`npm run build` all green. The `.app-doc-*` markup hooks and the title's
`text-violet-600` class are preserved, so `DocumentListView.spec.ts` (17 tests)
and the W16 responsive e2e contract are unaffected.

Note: not verified against the live compose stack (would need db/api/worker +
login for a CSS-only change); the standalone reproduction used the identical
computed style values the Tailwind classes compile to.

## 1.4 Docs

Updated `docs/frontend.md` §1.2 (utility-patterns vocabulary) with the
`.app-doc-grid` / `.app-doc-card` entry and corrected the stale
`shadow-xs rounded-xl` description in the §1.5 views table.
