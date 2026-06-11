# W16 — Mobile polish, PWA manifest, cross-device verification

**Date:** 2026-06-11
**Unit:** W16 (improvement plan §1.3.16)

## Context: session-died handoff

This unit was finished in two sessions. A first engineering session
implemented most of W16 and then died before verification and
documentation; a second session reviewed everything on disk as
unreviewed work, completed the gaps, and verified. The review found the
prior work largely sound, with one real bug (below).

## What the first session left (reviewed and kept)

- `frontend/public/manifest.webmanifest` — name/short_name "Library",
  `start_url`/`scope`/`id` `/`, `display: minimal-ui`, theme `#0b0c0c`
  (masthead black) / background white, 192 + 512 + 512-maskable icons.
- Icons: hand-drawn black/white "L." monogram from plain rectangles
  (`favicon.svg` source; no embedded typeface, nothing crown/crest
  shaped), exported to the three PNGs + `apple-touch-icon.png` +
  `favicon.ico`. Visually inspected: dignified, no GDS-like assets.
- `index.html`: manifest link, apple-touch-icon, SVG+ICO favicons,
  `theme-color` meta matching the manifest, `viewport-fit=cover`.
- `main.scss`: `env(safe-area-inset-*)` padding on masthead /
  service-navigation / footer; ≥44px hit areas for the masthead link,
  doc-list title links and upload-row links.
- Playwright: third project added (`tablet-webkit`, iPad portrait)
  alongside `chromium` and `mobile-webkit` (iPhone 14 pinned to 375px);
  new `e2e/responsive.spec.ts` (no horizontal overflow on /login, /,
  /upload; 320px floor re-check on mobile; nav reachable per viewport);
  `library.spec.ts` made viewport-agnostic (Menu-toggle-aware nav
  helper, duplicate-upload tolerant). Skip-when-no-`E2E_BASE_URL` kept.
- `src/__tests__/pwa.spec.ts` — asserts the index.html links, manifest
  required fields, icon files existing, colour consistency.
- CI `e2e` job already installed `chromium webkit` — no change needed.

## What the second session fixed/added

- **Bug:** `favicon.svg`'s XML comment contained the literal phrase
  "GDS Transport", which `npm run check:assets` (correctly) rejects —
  the first session's own licence gate failed on its own comment.
  Reworded; gate now passes.
- Touch-target sweep gaps: the summary-list **"Change" buttons**
  (`app-link-button`) and the standalone links ("Clear filters",
  "Open the PDF in a new tab", the two download links) had ~25px hit
  areas. Added a shared `.app-standalone-link` utility and padded
  `.app-link-button` (padding-block + negative margin-block — no layout
  shift).
- Documentation (was entirely missing): `docs/frontend.md` §1.6 updated
  to the three-project matrix + responsive spec, §1.7 mentions the PWA
  unit spec, new §1.8 (manifest/icons), §1.8.1 (display-mode rationale:
  `minimal-ui` over `standalone` because iOS standalone web apps have a
  history of file-input/camera quirks and hide browser navigation; URL
  bar retained is an acceptable trade), §1.8.2 (safe areas, touch
  targets), §1.8.3 (on-device checklist for the real iPhone/iPad: Add
  to Home Screen, icon-launch cookie persistence, camera capture,
  photo-library multi-upload, safe areas in both orientations, touch).
  `docs/architecture.md` W16 row → done.

## Verification

- `npm run test:unit -- --run` — 17 files, 128 tests passed.
- `npm run lint`, `npm run type-check` — clean.
- `npm run build` + `npm run check:assets` — OK (25 dist files clean).
- Full three-project Playwright matrix run locally against the real
  compose stack (db/migrate/api/worker + `vite preview`): **13 passed,
  2 skipped** (the 320px-floor test deliberately runs only on
  `mobile-webkit`) in ~15s; chromium exercised the fresh upload →
  indexed path, the WebKit projects the duplicate path. Stack torn down
  with `down -v` afterwards.

## Decisions

- No service worker / offline support: the archive is server-backed and
  private; offline caching adds risk for no family-scale benefit.
- `minimal-ui` display mode — see docs/frontend.md §1.8.1.
- Lighthouse installability is not in CI (needs a served origin and
  headed Chrome); `pwa.spec.ts` + the Playwright matrix cover the same
  regression surface.
