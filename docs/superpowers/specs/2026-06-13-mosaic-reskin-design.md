# Design: Reskin the `library` frontend to the Mosaic design language

**Date:** 2026-06-13
**Status:** Approved design — ready for implementation planning.

## Problem

The library web app's current theme (the GOV.UK Design System, `govuk-frontend`)
is efficient but visually harsh — black bars, 1px borders, bright-yellow focus
states. The user wants to replace it wholesale with the **Mosaic** admin
dashboard design language (Cruip): Tailwind-based, Inter typeface, violet accent,
soft `rounded-xl` cards with shadows, dark mode, and a collapsible left sidebar +
top header shell.

This is a **presentation-layer swap only**. The FastAPI backend, REST API
contracts, the frontend API client layer (`src/api/`), Pinia stores, router
*logic*, and the auth/session/CSRF flow are all untouched.

## Key insight: replicate the journal recipe

The sibling project `journal-insights` (`/Users/john/projects/journal/webapp`)
has **already** performed this exact migration to Mosaic, and runs an **identical
stack**:

| | library | journal |
|---|---|---|
| Vue | 3.5.32 | 3.5.32 |
| Vite | 8.0.8 | 8.0.8 |
| Pinia | 3.0.4 | 3.0.4 |
| vue-router | 5.0.4 | 5.0.4 |
| Vitest | 4.1.4 | 4.1.4 |

So this is not a from-scratch port. The shell components, the Tailwind 4
CSS-first `@theme` token block, and `utility-patterns.css` can be ported over
almost verbatim from journal and then adapted to library's routes and features.

Reference files in journal to copy/adapt:
- `src/assets/main.css` — `@theme` design tokens
- `src/assets/utility-patterns.css` — `.btn`, `.form-*` component classes
- `src/components/layout/AppSidebar.vue`, `AppHeader.vue`, `ThemeToggle.vue`
- `src/layouts/DefaultLayout.vue` — shell wrapper
- `src/App.vue` — public-route-bypasses-shell conditional
- `vite.config.ts` — `@tailwindcss/vite` wiring
- `journal/260410-mosaic-migration.md` — journal's own decision log

## Decisions (locked)

1. **Full Mosaic shell** — adopt the collapsible left sidebar + top header +
   `DefaultLayout` wrapper. The current horizontal top-nav (masthead + service
   navigation) is replaced. Nav destinations move into the sidebar.
2. **Dark mode included** — port Mosaic's `ThemeToggle` (`@vueuse useDark()` +
   `.dark` class on `<html>`); every component gets `dark:` variants.
3. **Thin Mosaic wrappers, same API** — rebuild each `Gov*` form component as a
   Mosaic-styled `App*` equivalent that preserves the existing props/emits, so
   views only swap imports. Accessibility patterns are preserved.
4. **Big-bang on a single feature branch** — the app is only 6 views; an
   all-at-once migration is cleaner than staging. Tests stay green as the gate.

## Architecture

### Build & styling setup

**Remove:** `govuk-frontend`, `sass-embedded`, `@fontsource/inter`, and
`src/styles/main.scss`.

**Add:** `tailwindcss@4`, `@tailwindcss/vite`, `@tailwindcss/forms`,
`@vueuse/core`. Wire `@tailwindcss/vite` into `vite.config.ts`. No PostCSS, no
`tailwind.config.ts` — CSS-first config, matching journal.

**Port from journal:**
- `src/assets/main.css` — the `@theme` block (gray / violet / sky / green / red /
  yellow palettes; Inter via Google Fonts `@import`; type scale; custom variants
  `dark` and `sidebar-expanded`; `@plugin "@tailwindcss/forms" { strategy: base }`).
- `src/assets/utility-patterns.css` — `.btn`/`.btn-lg`/`.btn-sm`/`.btn-xs`,
  `.form-input`, `.form-textarea`, `.form-select`, `.form-checkbox`, `.form-radio`,
  `.form-switch`, `.no-scrollbar`.

**Update** `frontend/scripts/check-assets.mjs` — its current job is to guard
against GOV.UK Transport-font / crown-imagery leaking into the bundle. That check
becomes obsolete; replace it with an appropriate check for the new bundle (or
remove it) and update any CI step that runs it.

### The shell (ported + adapted from journal)

| File | Purpose / library adaptation |
|---|---|
| `src/components/layout/AppSidebar.vue` | Collapsible left sidebar. Nav items: **Documents** (`/`), **Upload** (`/upload`), **Settings** (`/settings`), **Sign out**. Search is **not** a sidebar item (stays a modal). localStorage-persisted collapse, mobile overlay + backdrop, ESC/click-outside. |
| `src/components/layout/AppHeader.vue` | Sticky top header: hamburger (mobile), search-modal trigger, `ThemeToggle`, user/profile menu containing Sign out. |
| `src/components/layout/ThemeToggle.vue` | `@vueuse useDark()`, toggles `.dark` on `<html>`. |
| `src/layouts/DefaultLayout.vue` | Owns `sidebarOpen` state; renders `AppSidebar` + `AppHeader` + `<slot/>`; content in `px-4 sm:px-6 lg:px-8 py-8 w-full max-w-[96rem] mx-auto`. |
| `src/App.vue` | Conditional shell: **public routes (`/login`) bypass `DefaultLayout`** and render a centered Mosaic card; authenticated routes render inside `DefaultLayout`. Reuses the existing `useAuthStore` / auth guard unchanged. |

### Component layer: `Gov*` → `App*`

Each existing govuk wrapper (`src/components/govuk/`) is rebuilt as a
Mosaic-styled component with the **same props/emits**, so view changes are
mostly import swaps.

| govuk wrapper | new component | notes |
|---|---|---|
| GovButton | AppButton | `.btn` + variant classes (primary/secondary/warning) |
| GovInput | AppInput | `.form-input`; label/hint/error; optional `<datalist>` |
| GovTextarea | AppTextarea | `.form-textarea` |
| GovSelect | AppSelect | `.form-select`, `{value,text}[]` |
| GovCheckboxes | AppCheckboxes | `.form-checkbox`; Vue-driven conditional reveals |
| GovRadios | AppRadios | `.form-radio`; conditional reveals |
| GovDateInput | AppDateInput | **Keep** the 3-field pattern, ISO `YYYY-MM-DD` in/out (parity over Flatpickr swap) |
| GovErrorSummary | AppErrorSummary | preserve focus-on-mount + jump-to-field a11y |
| GovErrorMessage | AppErrorMessage | standalone field error |
| GovNotificationBanner | AppBanner | success/info; `role="alert"` |
| GovTag | AppBadge | Mosaic pill badges; map the existing colour set |
| GovPagination | AppPagination | Mosaic numeric pagination; same `@change` emit |
| GovSummaryList | AppSummaryList | key/value rows + per-row "Change" links |
| GovPanel | AppPanel | confirmation panel |
| GovDetails | AppDetails | native `<details>` disclosure |
| GovBackLink | AppBackLink | RouterLink / `<a>` |
| GovFileUpload | AppFileUpload | drop-zone, `File[] \| null` v-model |
| GovServiceNavigation | — | replaced by AppSidebar / AppHeader |

Custom components retained and restyled: `SearchModal.vue` (Mosaic
`ModalSearch` look, keeps `<dialog>` + `/`-key shortcut), `AppProgressBar.vue`
(recoloured to violet).

Accessibility carried over: error-summary focus management, labels + hints,
44px minimum touch targets, visible focus states.

### Views (logic unchanged, markup/styling reworked)

| View | Route | Reskin |
|---|---|---|
| DocumentListView | `/` | Tiles → Mosaic `bg-white dark:bg-gray-800 shadow-xs rounded-xl` cards in responsive grid; restyled search/filter bar; AppPagination; restyled empty state. |
| DocumentDetailView | `/documents/:id` | Two-column: preview pane (PDF/image) + AppSummaryList metadata card; inline edit via `App*` inputs. |
| DocumentDeleteView | `/documents/:id/delete` | Mosaic danger/confirmation card with destructive AppButton + cancel link. |
| UploadView | `/upload` | Mosaic drop-zone (AppFileUpload) + restyled per-file `AppProgressBar`; status polling UI unchanged. |
| SettingsView | `/settings` | Mosaic settings card with AppCheckboxes for dashboard-field toggles. |
| LoginView | `/login` | Centered Mosaic card on a gray background, **no shell**; AppInput + AppButton; error summary. |

## Testing

- Update Vitest component tests and Playwright e2e for the new markup/selectors.
  Logic-level assertions largely survive; class- and DOM-structure selectors
  change. Tests are the regression gate and must stay green.
- Verify focus management, keyboard nav, and dark-mode rendering manually for the
  shell and forms.

## Documentation & journal

- Rewrite `frontend/docs/frontend.md` to describe the Mosaic architecture
  (shell, tokens, `App*` components). Archive the old GOV.UK-oriented content per
  the docs convention (`git mv` into `docs/archive/` with a superseded header) if
  it is worth preserving as a decision record.
- Add a dated `journal/` entry capturing the migration decisions.

## Out of scope

- Backend, API contracts, data shapes.
- Auth/session/CSRF behaviour (cookie names, guards, endpoints).
- Charts (library has none today).
- Any new product features — this is purely a visual reskin.

## Build sequence (high level — detailed plan to follow)

1. Branch. Swap build/styling foundation (remove govuk/sass, add Tailwind 4 +
   plugins, port `main.css` + `utility-patterns.css`, wire vite). Get a blank
   styled app booting.
2. Port the shell (sidebar, header, theme toggle, DefaultLayout, App.vue
   conditional). Wire library's nav + auth store.
3. Build the `App*` component library (one-for-one against `Gov*`).
4. Reskin views one at a time, swapping imports to `App*`.
5. Restyle SearchModal + AppProgressBar.
6. Update `check-assets.mjs` / CI; update tests; update docs + journal.
