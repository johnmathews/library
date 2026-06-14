# 1. Dashboard tile redesign — scoped run

Focused, single-view aesthetic improvement (not a full codebase cycle). Run via
the interactive engineering-team workflow; the lead engineer diagnosed and
implemented directly given the narrow, low-risk scope.

## 1.1 Diagnosis

The documents dashboard (`frontend/src/views/DocumentListView.vue`) rendered
tiles as `bg-white` cards with `shadow-xs` and a `border-gray-200` border on a
`gray-100` page. The `shadow-xs` token is overridden to near-invisible
(`main.css`), so white-on-gray-100 gave no perceptible elevation — tiles blended
into the background. Stack: Vue 3 + Tailwind v4 (Mosaic template), violet accent,
Inter, dark-mode aware.

## 1.2 Decisions (confirmed with user)

- **Direction:** elevated cards (layered shadow + hover lift), cohesive with the
  existing Mosaic language — not a reskin.
- **Scope:** tiles + spacing polish; no layout/IA changes.

## 1.3 Work unit — W1: elevate the document tiles · DONE

- `utility-patterns.css`: `.app-doc-card` elevation (radius, layered resting
  shadow, hover lift + violet border), dark-mode shadow/border overrides,
  `prefers-reduced-motion` guard, `.app-doc-card__title a` 2-line clamp.
- `DocumentListView.vue`: removed redundant inline shadow/radius/hover classes,
  added thumbnail `border-b` divider, `p-4 → p-5` body padding, title
  `font-medium → font-semibold` (kept `text-violet-600`).

## 1.4 Verification

- Design iterated on a faithful standalone reproduction (light + dark
  before/after screenshots) — tiles now visibly lift.
- Real app: `lint`, `type-check`, `vitest run` (170/170, incl. the 17
  `DocumentListView` tests), `build` — all green.
- Acceptance contracts preserved: `.app-doc-*` hooks, title `text-violet-600`,
  W16 responsive grid.
- Not run against the live compose stack (CSS-only; standalone repro used the
  identical computed values).

## 1.5 Docs & journal

- `docs/frontend.md` §1.2 + §1.5 updated.
- `journal/260614-dashboard-tile-elevation.md` added.

## 1.6 Status

Complete. No follow-ups outstanding.
