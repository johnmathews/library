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
  and the **page canvas**. The light-mode `body` background is a per-user
  preference: `App.vue` sets `<html data-canvas="…">` from the stored tone, and
  `:root[data-canvas='…']` tokens map each tone to `--app-canvas` (used by
  `body { background-color: var(--app-canvas) }`). The default (no attribute) is
  `gray-200`, so white document tiles read as elevated surfaces. Dark mode
  ignores the tone (`.dark body` stays `gray-900`). Tones: `neutral` (default),
  `light`, `soft`, `slate`, `sand`, `mist` — see `BACKGROUND_TONES` in
  `src/api/settings.ts` and `/api/settings/appearance` (api.md §1.10.3).

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
  by `DocumentListView` and its tests. The `__thumbnail` box keeps a fixed
  `aspect-[4/3]`; how the (tall, A4) first-page image fits it is a per-user
  preference (`auth.tilePreview`): `full_width` (default) fills the width with
  `object-cover object-top` and crops the lower page, `whole_page` letterboxes
  the full page with `object-contain`. In `full_width` mode a
  `__thumbnail-fade` overlay (a `to-white dark:to-gray-800` bottom gradient)
  softens the hard cut where the crop meets the card body. See `TILE_PREVIEWS`
  in `src/api/settings.ts` and `/api/settings/appearance` (api.md §1.10.3).

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

Seven views (`src/views/`); routes and the auth guard are in
`src/router/index.ts`. Search is **not** a route — it is a navbar-triggered
modal.

| View | Route | Notes |
|------|-------|-------|
| `DocumentListView` | `/` (`documents`) | Dashboard **grid of document tiles** (elevated `.app-doc-card` surfaces — see §1.2); per-tile metadata is driven by the user's saved `dashboardFields` preference, rendered in a fixed canonical order; `AppPagination`; `AppBadge` tags; a one-shot flash `AppBanner`. The whole tile is a click target via a **stretched title link** (`after:absolute after:inset-0` over the `relative` card — a single anchor, no nested links). Tiles with no thumbnail show the file-type label, except PDFs with no thumbnail (unrenderable, usually password-protected) which show a **padlock placeholder** (`isLockedPdf`). All search/filter state lives in the URL query. |
| `DocumentDetailView` | `/documents/:id` (`document-detail`) | Leads with a full-width **hero header card**: the title (`h1#document-title`), a labelled **stat row** (Kind · Sender · Document date · Amount, em-dash for empty values), and the document's **tags as colour-varied `AppBadge` pills** (colour derived from the tag name via `tagColour`, so it's stable across renders). Below the hero, **two columns on desktop**: the **metadata card on the left** and the **preview pane on the right**. The metadata is split into **themed groups** (`fieldGroups` + `ACCENT`) — Content, Classification, Sender & dates, Financial, and a read-only System group — each a sub-panel with an accent left rail, faint tint and uppercase heading so the metadata *types* are distinguishable rather than one uniform list. Fields lay out in a **two-column grid** (`sm:grid-cols-2`; long fields and any open editor span both columns) with **larger values** (`text-base`) under **smaller uppercase labels** (`text-xs`); Amount renders as an emphasised figure and Status as a coloured pill (`statusAccent`). Inline per-row edit via `App*` inputs is unchanged (PATCH only the edited field; the editable Tags row stays plain text; each row keeps its `row-<field>`/`row-value`/`.app-link-button` hooks). The **extracted OCR text** lives in its **own panelled card** below the preview — an `AppDetails` disclosure over a scrollable monospace inset. When the document belongs to a recurring **series** (same sender + kind), a **`DocumentSeriesTrend`** panel (`v-if="doc"`) renders below: it fetches `GET /api/documents/{id}/series` on mount and draws a **Chart.js line chart** of the series' dated amounts with **this document's point highlighted** (`#dc2626`, matched by `document_id`, others `#2563eb`), plus a one-line verdict (e.g. *"≈6% above usual · trend rising"*) and the cadence label; it **self-hides** (renders nothing) when the series is `insufficient` or the fetch errors, so the page is unchanged for documents without a qualifying series. See [ask.md §1.7](ask.md). The preview card has a slim **header bar** with **Open** (new tab → inline URL) and **Download** (attachment → searchable PDF when present, else original) buttons, so the document window itself stays chrome-free. On `lg+` the PDF preview is the browser-native `<iframe>` whose src carries `#toolbar=0&navpanes=0&view=FitH` (hides the native viewer toolbar on Chrome/Edge); **Firefox ignores that fragment**, so there the iframe is nudged up inside an `overflow-hidden` wrapper to **clip its toolbar off the top edge** (`hidePdfToolbar`, UA-gated — Chrome/Edge keep the full height). On small screens — where the native viewer renders wider than the viewport and ignores `FitH` (notably iOS Safari) — it swaps to the **fit-width first-page thumbnail** (`thumbnailUrl`, gated on `has_thumbnail`), wrapped in a link that opens the PDF for full-res reading. A PDF with **no** thumbnail couldn't be rendered (almost always password-protected), so mobile shows a **clickable padlock placeholder** that opens the PDF — the browser then prompts for the password; the desktop iframe can prompt inline. Stacks on mobile, preview first; both columns are `min-w-0` and text containers `break-words` so long titles/values wrap rather than widen the page (which made iOS Safari zoom in). |
| `DocumentDeleteView` | `/documents/:id/delete` (`document-delete`) | A confirmation page (its own URL, not a JS modal) with a destructive `AppButton` + `AppBackLink` cancel. |
| `UploadView` | `/upload` (`upload`) | `AppFileUpload` drop-zone; each file uploads independently with its own `AppProgressBar`, then polls until `indexed`/`failed`; duplicate/error states via `AppBanner`/`AppErrorSummary`. |
| `AskView` | `/ask` (`ask`) | Natural-language question box (`AppTextarea` `#ask-question` + `AppButton` `#ask-submit`, "Asking…" while pending) → `POST /api/ask` (`src/api/ask.ts`). Renders the cited answer: the prose `#ask-answer` plus citation cards, each a `RouterLink` to the cited `document-detail`; shows `used_tools`/`cost_usd` subtly. Errors (notably the 503 "no API key" case) surface in `AppErrorSummary`. See [ask.md](ask.md). |
| `SettingsView` | `/settings` (`settings`) | Tabbed settings (`role="tablist"`). **Dashboard** tab: `AppCheckboxes` for the dashboard-field toggles (items from `DASHBOARD_FIELDS`), explicit save → `PUT /api/settings`. **Appearance** tab: page-canvas tone swatches (`BACKGROUND_TONES`) **and** a document-tile preview choice (`TILE_PREVIEWS`: full-width top crop, the default, vs whole-page letterbox), both applying live and auto-saving per click → `PUT /api/settings/appearance` (optimistic store update so the canvas/tiles repaint instantly; reverts on failure). Both surface success/error via `AppBanner`/`AppErrorSummary`. |
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

The inline filter bar (below) is visible at all screen sizes. From the `sm`
breakpoint up the pill row is always shown; below it the pills collapse behind a
**Filters** toggle (with an active-filter count badge) that expands them inline,
so status and multi-tag filtering stay available on mobile. The modal remains
available at any size (e.g. via the `/` shortcut) and writes the same URL query.

### Dashboard filter bar (`src/components/DocumentFilterBar.vue`)

An always-visible search-and-filter bar rendered by `DocumentListView` in the
dashboard hero area (replacing the old plain-text "Filtered by …" summary line).
The URL remains the single source of truth; all reading/writing of query
parameters goes through `src/utils/documentQuery.ts` (`parseDocumentQuery` /
`buildDocumentQuery` / `hasActiveFilters`), so the modal and the bar stay
in sync automatically.

- **Search input:** debounced 300 ms — typing pushes `?q=` via
  `router.replace`; pressing Enter applies immediately via `router.push`.
- **Filter pills:** Kind, Sender, Date range, Tag (multi-select —
  `?tag=a&tag=b`), and a **More** pill covering Language + Status.
- **Active-filter chips:** each applied filter renders as a removable chip
  below the pill row; a **Clear all** button removes every active filter at
  once.
- **Mobile:** below `sm`, a **Filters** toggle (with a count badge) collapses
  the pill row and expands it inline on tap; the search input and chips stay
  visible at every width.
- **`FilterPill` primitive** (`src/components/app/FilterPill.vue`, exported
  from `@/components/app`): a reusable controlled popover — rounded button +
  slotted dropdown panel, `v-model:open`, closes on Escape or outside
  mousedown.
- **`status` filter** and **multi-tag** (`tags: string[]`) are new additions
  to `AppliedFilters`; the `DOCUMENT_STATUSES` options array lives in
  `src/api/documents.ts`.

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
