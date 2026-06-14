# Frontend

The Library web UI: a Vue 3 single-page app styled with the **Mosaic** design
language (Cruip) — Tailwind 4, the Inter typeface, a violet accent, soft
`rounded-xl` cards, a collapsible left-sidebar + top-header shell, and dark
mode.

> The app was reskinned from the GOV.UK Design System to Mosaic on 2026-06-13.
> The reskin was a **presentation-layer swap only** — the FastAPI backend, REST
> API contracts, the frontend API client layer (`src/api/`), the Pinia stores,
> the router *logic*, and the auth/session/CSRF flow were all untouched. The
> retired GOV.UK frontend is preserved at
> [archive/frontend-govuk.md](archive/frontend-govuk.md) for its decision
> record (and the migration is journalled in `journal/260613-mosaic-reskin.md`).

## 1.1 Stack

| Layer | Choice |
|-------|--------|
| Framework | Vue 3.5 (`<script setup lang="ts">`, Composition API) |
| Build | Vite 8 (Rolldown) + `@tailwindcss/vite` |
| Styling | Tailwind CSS 4, **CSS-first** — config lives in `@theme` in `src/assets/main.css`; there is **no `tailwind.config.ts`** and no PostCSS |
| Forms | `@tailwindcss/forms` (`strategy: base`) |
| Typeface | Inter, loaded via a Google Fonts `@import` in `main.css` |
| Routing | vue-router 5 (history mode) |
| State | Pinia 3 |
| Dark mode | `@vueuse/core` (`useDark`) toggling `.dark` on `<html>` |
| Unit tests | Vitest 4 + `@vue/test-utils`, jsdom |
| E2E | Playwright |

Everything lives in `frontend/`. Two serving modes:

- **Dev:** `npm run dev` — Vite on `:5173`, proxying `/api` and `/healthz` to
  the backend on `localhost:8000` (see `vite.config.ts`).
- **Production:** the Docker image builds the SPA and the FastAPI process serves
  `frontend/dist` itself (hashed `/assets` immutable, everything else falling
  back to `index.html`) — see [deployment.md](deployment.md). No separate web
  server.

## 1.2 Design tokens and utility classes

All design tokens are CSS-first. Two files in `src/assets/`, imported once from
`src/main.ts` (`import './assets/main.css'`):

### `src/assets/main.css`

- **Font:** `@import url('https://fonts.googleapis.com/css2?family=Inter…')` —
  Inter 400/500/600/700 from Google Fonts. `--font-inter` is the `@theme` font
  token; `html { @apply font-inter antialiased }` in the base layer.
- **`@theme` block** — the Mosaic palette and type scale as CSS custom
  properties (which is what makes them available as Tailwind utilities):
  - Palettes: **gray**, **violet** (the accent), **sky**, **green**, **red**,
    **yellow** — each a full 50–950 ramp. (Library does not ship journal's
    `fuchsia` palette.)
  - A full `--text-*` type scale (xs → 6xl) with per-step line-height and
    letter-spacing; a custom `--shadow-sm`; `--breakpoint-xs: 480px`.
- **Custom variants:** `@custom-variant dark (&:is(.dark *))` and
  `@custom-variant sidebar-expanded (&:is(.sidebar-expanded *))` — these power
  every `dark:` and `sidebar-expanded:` utility used across the app.
- **Forms plugin:** `@plugin "@tailwindcss/forms" { strategy: base }`.
- **Base layer:** Tailwind-v4 border-color compat shim, `font-inter` on `html`,
  and the page background `body { @apply bg-gray-100 dark:bg-gray-900 … }`.

### `src/assets/utility-patterns.css`

Imported into `main.css` as `layer(components)`. Mosaic's component-class
vocabulary that the `App*` components and views compose:

- **Buttons:** `.btn` (+ `.btn-lg` / `.btn-sm` / `.btn-xs`) — shared inline-flex
  pill base; views/`AppButton` add the colour (`bg-violet-500`, etc.).
- **Form controls:** `.form-input`, `.form-textarea`, `.form-select`,
  `.form-checkbox`, `.form-radio`, `.form-multiselect`, `.form-switch` — the
  base + dark-mode styling for every field control.
- **Typography helpers** (`.h1`–`.h4`) and `.no-scrollbar`.
- **Dashboard tiles:** `.app-doc-grid` (the responsive 1/2/3/4-column document
  grid — column count per viewport is the W16 acceptance contract) and
  `.app-doc-card` (the elevated tile surface: rounded corners, a layered drop
  shadow that lifts the white tile off the gray page, and a hover state that
  raises it 3px and warms the border to violet). Dark mode swaps the shadow for
  a gray-800-on-gray-900 surface plus border, since shadows don't read against
  a near-black page; `prefers-reduced-motion` drops the lift. `.app-doc-card__*`
  hooks (`__title`, `__thumbnail`, `__meta`, …) are an acceptance contract used
  by `DocumentListView` and its tests.

## 1.3 The shell

Authenticated routes render inside a Mosaic shell; the public `/login` route
renders bare.

### `src/App.vue`

Branches on `route.meta.public`. Public routes render a lone `<RouterView/>`;
everything else is wrapped in `DefaultLayout`:

```vue
<RouterView v-if="isPublicRoute" />
<DefaultLayout v-else>
  <RouterView />
</DefaultLayout>
```

Auth gating is **not** done here — `router.beforeEach(authGuard)` (see
`src/router/index.ts`) already redirects unauthenticated users to `/login`
before any shell renders, so any non-public route reaching `App.vue` is
guaranteed authenticated.

### `src/layouts/DefaultLayout.vue`

The shell wrapper. Owns the mobile `sidebarOpen` state, renders `AppSidebar` +
`AppHeader` + a `<main>` whose content sits in
`px-4 sm:px-6 lg:px-8 py-8 w-full max-w-[96rem] mx-auto`, and **mounts
`SearchModal`** — wiring `AppHeader`'s `open-search` emit to
`searchModal?.open()`.

### `src/components/layout/AppSidebar.vue`

Collapsible left sidebar. Props `{ sidebarOpen }`, emits `close-sidebar`.

- **Nav items** (RouterLink, gradient violet active state): **Documents** (`/`),
  **Upload** (`/upload`), **Settings** (`/settings`). Each has a
  `data-testid="sidebar-*-link"`. Search is **not** a sidebar item (it is a
  navbar-triggered modal, §1.5).
- **Collapse state** persists to `localStorage['sidebar-expanded']`, mirrored
  onto `body.sidebar-expanded` (seeded by an inline script in `index.html` to
  avoid a flash); when unset it defaults from a `matchMedia('(min-width:1024px)')`
  check. A desktop expand/collapse button toggles it.
- **Mobile:** off-canvas drawer with a `bg-gray-900/30` backdrop; closes on
  click-outside, ESC, or route change.

### `src/components/layout/AppHeader.vue`

Sticky top header. Props `{ sidebarOpen }`, emits `toggle-sidebar` and
`open-search`. Contains: the mobile **hamburger** (`aria-controls="sidebar"`), a
**search trigger** button (`data-testid="header-search-button"`, the modal entry
point), the **`ThemeToggle`**, and a **user menu** showing
`auth.user?.display_name || username` with **Settings** and **Sign Out** (calls
`auth.logout()` then routes to `login`).

### `src/components/layout/ThemeToggle.vue`

A `sr-only` checkbox bound to `@vueuse/core`'s `useDark({ selector: 'html' })`,
which adds/removes `.dark` on `<html>` and persists the choice. Sun/moon SVGs
swap via `dark:hidden` / `hidden dark:block`.

## 1.4 The `App*` component library

Thin, Mosaic-styled SFC wrappers live in `src/components/app/`, exported from
the barrel `src/components/app/index.ts`; shared TypeScript interfaces
(`SelectItem`, `ChoiceItem`, `ErrorSummaryItem`, `SummaryListRow`,
`SummaryListAction`) live in `src/components/app/types.ts` (re-exported by the
barrel). Views import from `'@/components/app'`.

Each `App*` component **preserves the public API of the `Gov*` wrapper it
replaced** — same props, emits, slots, and v-model — so the view migration was
largely an import swap. Notable shared conventions carried over: the field error
prop is `errorMessage`; option/choice lists are passed as `items`; form
components use `defineModel()`.

| Component | Replaces | What it does |
|-----------|----------|--------------|
| `AppButton` | GovButton | `.btn` + `variant` (`primary` violet / `secondary` / `warning` red / `inverse`); renders `<RouterLink>` for `to`, `<a role=button>` for `href`, else `<button>`. `preventDoubleClick` retained. |
| `AppInput` | GovInput | `.form-input` with label/hint/error wiring + `aria-describedby`/`aria-invalid`; optional `list` for a `<datalist>`. |
| `AppTextarea` | GovTextarea | `.form-textarea` with the same label/hint/error wiring. |
| `AppSelect` | GovSelect | `.form-select`, options from `items: SelectItem[]` (`{value,text}`). |
| `AppCheckboxes` | GovCheckboxes | `<fieldset>`/`<legend>` + `.form-checkbox` rows from `items: ChoiceItem[]`; Vue-driven conditional reveals via `conditional-<value>` slots; `string[]` model. |
| `AppRadios` | GovRadios | as `AppCheckboxes`, `.form-radio`, scalar model. |
| `AppDateInput` | GovDateInput | **3-field** day/month/year inputs; v-model is an ISO `YYYY-MM-DD` string or `null`. Parse/format logic kept verbatim (no date-picker dependency). |
| `AppBadge` | GovTag | Mosaic pill badge; maps GovTag's `colour` set onto Mosaic `{bg,text}` pairs. |
| `AppPanel` | GovPanel | Violet confirmation panel (title + body slots). |
| `AppDetails` | GovDetails | Native `<details>` disclosure with a violet summary. |
| `AppBackLink` | GovBackLink | Chevron back link; `<RouterLink>`/`<a>`. |
| `AppBanner` | GovNotificationBanner | `role="alert"` left-border banner; `variant="success"` → green, else info/sky; focuses on mount. |
| `AppErrorSummary` | GovErrorSummary | Red summary card listing `errors: ErrorSummaryItem[]`; **focuses itself on mount** and each link moves focus to its field (a11y preserved). |
| `AppErrorMessage` | GovErrorMessage | Standalone field-error paragraph with a visually-hidden "Error:" prefix. |
| `AppSummaryList` | GovSummaryList | Key/value rows with optional per-row "Change" action links. |
| `AppPagination` | GovPagination | Numeric pagination; props `{ page, totalPages }`, emits `change(page)`. |
| `AppFileUpload` | GovFileUpload | Drop-zone; v-model is `File[] \| null`; `multiple`/`accept` props. |
| — | GovServiceNavigation | **Removed** — its job is now split between `AppSidebar` (nav) and `AppHeader` (search trigger, theme toggle, user menu). |

Two retained custom components, restyled to Mosaic:

- `src/components/SearchModal.vue` — the search-and-filter modal (§1.5).
- `src/components/AppProgressBar.vue` — upload progress bar; violet
  (`bg-violet-500`) fill, `role="progressbar"` with `aria-valuenow`/`aria-label`.

## 1.5 Views and routes

Six views (`src/views/`); routes and the auth guard are in
`src/router/index.ts`. Search is **not** a route — it is a navbar-triggered
modal.

| View | Route | Notes |
|------|-------|-------|
| `DocumentListView` | `/` (`documents`) | Dashboard **grid of document tiles** (elevated `.app-doc-card` surfaces — see §1.2); per-tile metadata is driven by the user's saved `dashboardFields` preference, rendered in a fixed canonical order; `AppPagination`; `AppBadge` tags; a one-shot flash `AppBanner`. All search/filter state lives in the URL query. |
| `DocumentDetailView` | `/documents/:id` (`document-detail`) | Two-column on desktop: **metadata card on the left** — a key/value `dl` with inline per-row edit via `App*` inputs (PATCH only the edited field) — and the **preview pane on the right** (browser-native PDF `<iframe>` / `<img>`). Stacks on mobile, preview first. |
| `DocumentDeleteView` | `/documents/:id/delete` (`document-delete`) | A confirmation page (its own URL, not a JS modal) with a destructive `AppButton` + `AppBackLink` cancel. |
| `UploadView` | `/upload` (`upload`) | `AppFileUpload` drop-zone; each file uploads independently with its own `AppProgressBar`, then polls until `indexed`/`failed`; duplicate/error states via `AppBanner`/`AppErrorSummary`. |
| `SettingsView` | `/settings` (`settings`) | A Mosaic settings card; `AppCheckboxes` for the dashboard-field toggles (items from `DASHBOARD_FIELDS` in `src/api/settings.ts`), save → `PUT /api/settings` → success `AppBanner`; error → `AppErrorSummary`. |
| `LoginView` | `/login` (`login`, `meta.public`) | **Bypasses the shell** — a centered `w-full max-w-md` Mosaic card on a `bg-gray-100 dark:bg-gray-900` background; `AppInput` + `AppButton` + `AppErrorSummary`. |

### Search modal (`src/components/SearchModal.vue`)

A native `<dialog>` (`showModal()`) mounted once in `DefaultLayout`. Opened
either by the header search button (`open-search` → `searchModal.open()`) or by
pressing **`/`** anywhere outside a form field. It exposes `open()` via
`defineExpose`, pre-fills its fields (`AppInput` query, `AppSelect`
kind/sender/tag/language fed lazily from the cached taxonomy endpoints,
`AppDateInput` from/to) from the current route query, and on submit pushes the
query to the documents route. Native dialog semantics give focus containment,
ESC-to-close and `::backdrop`; focus is handed back to the opener on close.
Layout lives in `.app-search-modal` (`utility-patterns.css`): a centered
`max-w-2xl` card on desktop, full-screen below 640px. It reasserts
`margin: auto` because Tailwind Preflight zeroes the margin that the browser
otherwise uses to centre a modal `<dialog>`.

## 1.6 Dark mode

Dark mode is class-based (`.dark` on `<html>`, driven by `ThemeToggle` /
`useDark`) and surfaced through the `dark` custom variant defined in `main.css`.
Every shell component, `App*` component, and view carries `dark:` variants, so
the whole app — backgrounds, cards, borders, text, form controls — responds to
the toggle. The user's choice is persisted by `useDark` (localStorage).

## 1.7 Tests and checks

- `npm run test:unit` — Vitest component/behaviour specs (`ThemeToggle` dark
  toggle, `AppSidebar` nav, `AppHeader` search/hamburger emits, the `App*` form
  components incl. error-summary focus, conditional reveals, date-input ISO
  emission), plus the unchanged API-client/store/router/view specs. Every view
  spec also asserts its page heading — the unit-speed guard for the
  acceptance-contract copy (`Sign in`, `Upload documents`, `Documents`, …).
- `npm run test:coverage` — Vitest with V8 coverage. Gated in CI:
  lines/statements/functions ≥ 85%, branches ≥ 75% (branches run structurally
  lower). CI surfaces the report on the run summary, as an HTML artifact, and
  as a sticky PR comment.
- `npm run type-check` (`vue-tsc`), `npm run lint` (ESLint).
- `npm run build && npm run check:assets` — `scripts/check-assets.mjs` was
  **repurposed** from a GOV.UK-licensing gate into a **govuk-residue gate**: it
  scans `dist/` and fails if any file name or text content reintroduces
  `govuk-`, GDS Transport, or crown/crest references (guarding against a partial
  reskin regression).
- `npm run test:e2e` — Playwright against the real stack.

## 1.8 What did not change

The reskin touched only the presentation layer. Unchanged:

- `src/api/` — `client.ts` (fetch + CSRF double-submit), `documents.ts`,
  `taxonomy.ts`, `settings.ts`. API contracts per [api.md](api.md).
- `src/stores/` — `auth.ts` (`useAuthStore`: `user`, `isAuthenticated`,
  `ensureLoaded()`, `login`/`logout`, `dashboardFields`, `applyPreferences`)
  and `flash.ts`.
- `src/router/index.ts` route table and `authGuard` logic (only the rendered
  shell around the routes changed).
- The backend, auth/session/CSRF behaviour, and the snippet/highlight XSS-safety
  helpers in `src/utils/`.
