# W9 — Frontend foundation: GOV.UK design system port

**Date:** 2026-06-10

## What landed

- govuk-frontend 6.2.0 consumed via Sass modules (`@use .../govuk/base
  with (...)` + per-component `@use`), Inter (latin + latin-ext, 400/700)
  self-hosted via @fontsource/inter, text-only "Library" masthead.
- 18 Vue wrappers in `frontend/src/components/govuk/` emitting the markup
  from govuk-frontend's own rendered fixtures, plus a
  `useGovukComponent()` composable for the JS-backed ones.
- App shell (skip link, masthead, service navigation, beta phase banner,
  width container, minimal footer), typed API client with CSRF
  double-submit, Pinia auth store, router guard, GOV.UK-pattern login
  page.
- 57 Vitest tests; `scripts/check-assets.mjs` licensing gate wired into
  CI after the frontend build.
- Docs: `docs/frontend.md`; architecture W9 row → done.

## Decisions

- **Licensing (R1):** GDS Transport and crown/crest assets are
  licence-restricted to gov.uk. Beyond the planned font substitution we
  found the **footer component CSS references `govuk-crest.svg`**, so we
  import components individually instead of the all-in-one index and ship
  our own minimal footer. The check script also greps for `crest`, not
  just transport/crown, and rejects any non-Inter web font in `dist/`.
- **JS init split:** Button, ErrorSummary, NotificationBanner,
  ServiceNavigation, FileUpload, SkipLink are initialised per component
  instance in `onMounted` (guarded by `isSupported()`, marker attribute
  cleared on unmount — v6 has no `destroy()`). Radios/Checkboxes
  conditional reveal is handled by Vue bindings instead of govuk JS to
  avoid two owners of the same DOM state. DateInput, Details, etc. are
  CSS-only in v6.
- **Sass keys used:** `$govuk-font-family`,
  `$govuk-include-default-font-face: false`, `$govuk-global-styles: true`;
  brand colour left default. v6's `govuk-functional-colour()` /
  `govuk-colour()` helpers style the custom masthead/footer.
- **Vite 8 quirks:** Rolldown's LightningCSS minifier rejects
  govuk-frontend's old-IE `(min-width: 0\0)` hack →
  `css.lightningcss.errorRecovery: true` strips it (no supported browser
  needs it). `quietDeps` silences upstream Sass deprecation noise.
  govuk-frontend ships no TS types → local
  `src/types/govuk-frontend.d.ts`.
- **Auth contract:** `X-CSRF-Token` header echoes the readable
  `library_csrftoken` cookie on unsafe methods only; `GET /api/auth/me`
  is called once per page load and cached in the store; guard preserves
  the original target in `?redirect=`.

## Deferred

- Playwright smoke (login → shell) deliberately deferred to W10, which
  brings the first real user flows worth driving end-to-end.
- Nav is minimal (Documents + Sign out); Upload route arrives with W10.
