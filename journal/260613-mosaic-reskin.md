# Mosaic reskin — GOV.UK → Mosaic design language

**Date:** 2026-06-13

Spec: `docs/superpowers/specs/2026-06-13-mosaic-reskin-design.md`
Plan: `docs/superpowers/plans/2026-06-13-mosaic-reskin.md`

## Motivation

The GOV.UK Design System served the build well — it is efficient, accessible,
and got us to a working app fast (W9). But visually it is harsh: black bars, 1px
borders, and bright-yellow focus states. For a personal family-document app it
felt **too brutal**. The goal here was a more pleasant, modern look without
touching anything below the presentation layer.

## Approach

Rather than design from scratch, we **replicated the proven Mosaic migration
from the sibling `journal-insights` webapp** (`/Users/john/projects/journal/
webapp`), which runs an *identical* stack (Vue 3.5, Vite 8, Pinia 3, vue-router
5, Vitest 4). That made this a port-and-adapt job, not a from-scratch reskin:
the Tailwind 4 token block, the `utility-patterns.css`, and the shell components
(`AppSidebar` / `AppHeader` / `ThemeToggle` / `DefaultLayout`) came over from
journal almost verbatim, then were trimmed to library's nav, routes, and auth
store.

This was a **presentation-layer swap only**. The FastAPI backend, REST
contracts, `src/api/` client, Pinia stores, router *logic*, and
auth/session/CSRF flow were untouched; tests were the regression gate.

## Decisions

- **Full Mosaic shell.** Adopted the collapsible left sidebar + sticky top
  header + `DefaultLayout` wrapper. The old horizontal GOV.UK masthead +
  service-navigation is gone; nav destinations (Documents / Upload / Settings)
  moved into the sidebar. `App.vue` keeps the public-route-bypasses-shell
  pattern so `/login` renders bare.

- **Dark mode included.** Ported Mosaic's `ThemeToggle` (`@vueuse/core`
  `useDark()` → `.dark` on `<html>`); the `dark` custom variant in `main.css`
  means every component carries `dark:` variants.

- **Thin `App*` wrappers that preserve the `Gov*` API.** Each GOV.UK wrapper was
  rebuilt as a Mosaic-styled `App*` component with the **same props, emits,
  slots, and v-model** (field error prop still `errorMessage`, option lists
  still `items`). Because the contract was preserved, the six views were
  near-pure import swaps (`@/components/govuk` → `@/components/app`).
  `GovServiceNavigation` was the one component dropped outright — its role split
  between `AppSidebar` and `AppHeader`.

- **Kept the 3-field ISO date input.** `AppDateInput` keeps GovDateInput's
  day/month/year fields and ISO `YYYY-MM-DD` in/out (parse/format logic copied
  verbatim) rather than swapping in Flatpickr — parity over a new dependency.

- **Kept search-as-modal.** `SearchModal` stays a native `<dialog>` opened by
  the header search button or the `/` key; it is *not* a sidebar/nav route. It
  was restyled and re-pointed at the `App*` form components but kept its
  behaviour and `defineExpose({ open })` contract.

## Structure of the work (5 phases)

1. **Foundation** — swapped deps (out: `govuk-frontend`, `sass-embedded`,
   `@fontsource/inter`; in: `tailwindcss`/`@tailwindcss/vite`/`@tailwindcss/forms`,
   `@vueuse/core`), wired `@tailwindcss/vite`, ported `main.css` + `utility-
   patterns.css`, and reseeded `index.html` (sidebar-expanded script, Mosaic
   theme-color).
2. **Shell** — `ThemeToggle`, `AppSidebar`, `AppHeader`, `DefaultLayout`, and the
   `App.vue` public-route conditional.
3. **`App*` library** — one Mosaic component per `Gov*` wrapper, behind the
   `src/components/app/index.ts` barrel with shared `types.ts`.
4. **View reskins** — the six views, swapping imports to `App*`; plus restyling
   `SearchModal` and `AppProgressBar` (now violet).
5. **Cleanup** — repurposed `check-assets.mjs`, updated tests, and rewrote the
   docs.

## Notable specifics

- **Tailwind 4 CSS-first, no config file.** Tokens live in an `@theme` block in
  `src/assets/main.css` (gray/violet/sky/green/red/yellow ramps, Inter, type
  scale, the `dark` and `sidebar-expanded` custom variants). No
  `tailwind.config.ts`, no PostCSS. Ported from journal, minus its
  journal-only `fuchsia` palette.
- **Temporary `govuk-frontend` build stub.** During the migration a stub stood
  in for `govuk-frontend` so the app kept building while views still imported
  `Gov*` components; it was removed once every view had moved to `App*`.
- **`check-assets.mjs` repurposed.** Formerly a GOV.UK *licensing* gate (no GDS
  Transport / crown / crest in `dist/`), it is now a **govuk-residue** gate: it
  fails the build if `govuk-`, GDS Transport, or crown/crest references reappear
  — catching a partial-reskin regression.

## Docs

- `docs/frontend.md` rewritten for the Mosaic architecture.
- Old GOV.UK doc archived to `docs/archive/frontend-govuk.md` with a superseded
  header (per the docs convention — kept for its decision record).

## Post-merge hardening (2026-06-14)

The unit suite and per-phase reviews were green, but **e2e went red on `main`**
after the local merge — the branch (`mosaic-reskin`) matched neither the `main`
nor `eng-**` push-trigger filter and no PR was opened, so the e2e gate never ran
until the merge landed. Reproduced locally against the real stack (Docker
backend + `vite preview` + Playwright) and fixed three reskin regressions the
unit tests couldn't catch:

- **Heading copy** the acceptance suite asserts: `LoginView` page heading was
  renamed `Sign in` → `Library` (restored; kept `Library` as a small brand
  wordmark) and `UploadView` `Upload documents` → `Upload` (restored). A pure
  reskin should never have changed user-facing text.
- **Dashboard grid breakpoints.** The `.app-doc-grid` rule died with
  `main.scss`; Tailwind `xl:grid-cols-4` gave 4 columns at 1280px where the W16
  contract requires 3. Re-added `.app-doc-grid` to `utility-patterns.css` with
  the original breakpoints (1 / 2@641 / 3@769 / 4@1400).
- **e2e helper bug** (mine): `openUploadPage` gated the mobile sidebar reveal on
  the upload link's visibility, but a transform-offscreen link still reports
  visible to Playwright — gate on the header hamburger instead.

Lesson: unit specs assert behaviour/markup, not heading copy or responsive
column counts — exactly the gap e2e exists to catch. Added unit-speed guards
(page-heading assertions in the Upload/List/Settings specs) so that class of
regression fails in milliseconds, not only in the 6-minute e2e matrix.

### CI improvements

- **Gate every branch.** `push` now triggers on `["**"]` (was `main` + `eng-**`)
  with a `cancel-in-progress` concurrency group, so feature work is e2e-gated
  before it reaches `main`.
- **Docker layer cache.** `e2e` and `compose-smoke` build the backend image via
  `docker buildx bake` reading a shared gha cache (`scope=library-image`) that
  the `docker` job writes (`mode=max`), instead of an uncached
  `compose up --build` each run. Cache/name miss falls back to a rebuild —
  slower, never broken. Plus a Playwright-browser cache.
- **Coverage surfaced and gated.** Frontend coverage was previously not measured
  at all; added `@vitest/coverage-v8` + config + a `test:coverage` script.
  Backend coverage was computed but buried in the log. Both now write a table to
  the job summary, upload an HTML artifact, and post a sticky PR comment
  (`marocchino/sticky-pull-request-comment`). Gates: backend `fail_under = 85`
  (currently ~95%); frontend lines/statements/functions ≥ 85, branches ≥ 75
  (branch coverage runs structurally lower).
