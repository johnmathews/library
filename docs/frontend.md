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
| PDF preview | `pdfjs-dist` (renders PDFs to canvas via `DocumentPdfPreview.vue`) |
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
- **`.filter-label`** — the shared filter/control-bar label recipe (uppercase-xs,
  gray, `mb-1`); one definition so every filter bar matches. Scoped to
  filter/control bars, **not** stacked forms (whose labels are baked into the
  `App*` inputs). See [frontend-view-principles.md](frontend-view-principles.md) §5.
- **`.card`** — the shared white/dark panel surface (background, `shadow-xs`,
  `rounded-xl`, hairline border). Carries **no padding** by design — callers add
  their own `p-4`/`p-5`/`p-6` so spacing stays per-view.
- **Typography helpers** (`.h1`–`.h4`), `.no-scrollbar` (hides the scrollbar
  entirely — used for app chrome), and `.thin-scrollbar` (keeps a subtle, thin
  scrollbar so an internal scroll region reads as scrollable — used by the Ask
  conversation thread list).
- **Dashboard tiles:** `.app-doc-grid` (the responsive 1/2/3/4-column document
  grid — the per-viewport *default* column count is the W16 acceptance contract;
  the desktop/wide counts read from the `--doc-grid-cols` CSS var, which
  DocumentListView sets from a per-machine "tiles per row" preference stored in
  `localStorage` under `library:doc-grid-cols`, falling back to 3/4 when "Auto".
  The phone band (`< 641px`) instead honours a separate `--doc-grid-cols-phone`
  var (values 1/2/3, default 2) set from `auth.phoneColumns` — a **server-synced**
  account preference (`phone_columns` in the `user.preferences` JSON blob, no DB
  migration) configured via **Settings → Appearance → Phone columns**; the
  tablet band stays fixed at 2 columns and the desktop `--doc-grid-cols` control
  is unaffected. On phones (`<= 640px`) the dashboard is also visually
  densified — reduced tile padding (`px-2 py-3` vs `sm:p-5`), a tighter
  inter-tile gap (`#dashboard-grid` gap `0.75rem` vs `1.5rem`), snug leading,
  and **abbreviated month names** in tile dates ("17 Sep 2019" rather than
  "17 September 2019", via a reactive `useMediaQuery('(max-width: 640px)')`);
  all of this reverts to the roomier desktop styling at `>= 640px`)
  and
  `.app-doc-card` (the elevated tile surface: rounded corners, a layered drop
  shadow that lifts the white tile off the gray page, and a hover state that
  raises it 3px and warms the border to violet). Dark mode swaps the shadow for
  a gray-800-on-gray-900 surface plus border, since shadows don't read against
  a near-black page; `prefers-reduced-motion` drops the lift. A tile whose
  document **kind** has a colour gets `.app-doc-card--accented` plus a
  `--card-accent` hex (per-user, from Settings → Appearance → Document type
  colours); the accent overrides the base border in both modes (adapted per
  surface with `color-mix`) and shifts on hover, while neutral kinds keep the
  gray/violet default. **Cascade-layer note:** `.app-doc-card` **owns its
  border** (`border: 1px solid …` in the rule, *not* a `border-gray-200` utility
  on the markup). `utility-patterns.css` is imported into Tailwind's `components`
  layer, which loses to the `utilities` layer regardless of specificity — so a
  Tailwind border utility on the tile would silently defeat the accent
  `border-color` (this is exactly the bug that made the accent invisible when it
  first shipped). Keeping the neutral, hover, and accent borders all in the one
  `components` layer lets normal specificity decide. Coloured tiles use a **2px**
  border (neutral tiles stay 1px) so the kind colour reads on high-DPI phones;
  `box-sizing:border-box` keeps the grid aligned. A real computed border
  colour+width check lives in `e2e/tile-border-colour.spec.ts` (jsdom can't
  resolve layered cascade, so the unit test only asserts the class hook).
  `.app-doc-card__*`
  hooks (`__title`, `__thumbnail`,
  `__meta`, …) are an acceptance contract used by `DocumentListView` and its
  tests. The `__thumbnail` box keeps a fixed
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
`SearchModal`** — wiring the `open-search` emits from both `AppHeader` and
`AppSidebar` to the same `searchModal?.open()`.

### `src/components/layout/AppSidebar.vue`

Collapsible left sidebar. Props `{ sidebarOpen }`, emits `close-sidebar` and
`open-search`.

- **Nav items** (RouterLink, gradient violet active state), in order:
  **Documents** (`/`), then the user's **pinned saved-view dashboards**
  directly beneath it as first-class links (`sidebar-dashboard-<id>` → `/`
  with the saved query; no "Saved views" heading and no separate subsection —
  hidden when none pinned; the management page is reached from the dashboard
  "Saved views" button instead — see `SavedViewsView` below),
  **Search** (a `<button data-testid="sidebar-search-button">`, not a link —
  it emits `open-search` to open the shared modal, §1.5; the click also
  bubbles through the nav list's `close-sidebar` handler so the mobile
  drawer dismisses as the modal opens),
  **Upload** (`/upload`), **New note** (`/notes/new`,
  `data-testid="sidebar-notes-link"`), **Charts** (`/charts`), **Ask** (`/ask`),
  **Jobs** (`/jobs`), **Projects** (`/projects`,
  `data-testid="sidebar-projects-link"`), **Settings** (`/settings`), —
  **for admins only** (`v-if="auth.isAdmin"`) — **Admin** (`/admin`,
  `data-testid="sidebar-admin-link"`), and finally **Recently Deleted**
  (`/deleted`, `data-testid="sidebar-deleted-link"`) kept at the bottom as a
  low-traffic destination. Each route link has a `data-testid="sidebar-*-link"`
  (the Search button deliberately uses `-button`, keeping it out of the
  `a[data-testid]` selectors the nav-order tests use). Pinned
  dashboards load reactively (`watch(auth.isAuthenticated)`) since the sidebar
  is a persistent shell that mounts before the router's async auth guard
  resolves.
- **Collapse state** persists to `localStorage['library:sidebar-expanded']`
  (legacy bare `sidebar-expanded` key still read once as a fallback), mirrored
  onto `body.sidebar-expanded` (seeded by an inline script in `index.html` to
  avoid a flash); when unset it defaults from a `matchMedia('(min-width:1024px)')`
  check. A desktop expand/collapse button toggles between **narrow (icons only)**
  and **wide (icons + text)** at **every** desktop width — the sidebar is no
  longer force-widened at `2xl` (that hid the toggle on large monitors).
- **Mobile:** off-canvas drawer with a `bg-gray-900/30` backdrop; closes on
  click-outside, ESC, or route change.

### `src/components/layout/AppHeader.vue`

Sticky top header. Props `{ sidebarOpen }`, emits `toggle-sidebar` and
`open-search`. Contains: the mobile **hamburger** (`aria-controls="sidebar"`), a
**search trigger** button (`data-testid="header-search-button"`, one of the
modal's entry points), the **`ThemeToggle`**, and a **user menu** showing
`auth.user?.display_name || username` with **Settings** and **Sign Out** (calls
`auth.logout()` then routes to `login`).

It also renders the **background-jobs indicator** (`#header-jobs-indicator`,
`[data-testid="header-jobs-button"]`), present only while `jobsStore.activeCount
> 0`: a spinner with a count badge that opens a dropdown of in-flight documents
(stage label per row) plus a **View all jobs** link to `/jobs`. Because the
button sits mid-cluster (search/theme/user-menu are to its right), the dropdown
**pins to the viewport's right edge below `sm`** (`fixed` + `max-w-[calc(100vw-1rem)]`)
so it can't overflow the screen on a phone, and reverts to the under-button
`absolute` anchor at `sm`+.

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
| `AppButton` | GovButton | `.btn` + `variant` (`primary` violet / `secondary` / `warning` red / `inverse`); optional `size` (`sm` → `.btn-sm`, `lg` → `.btn-lg`, default `.btn`); renders `<RouterLink>` for `to`, `<a role=button>` for `href`, else `<button>`. `preventDoubleClick` retained. |
| `AppInput` | GovInput | `.form-input` with label/hint/error wiring + `aria-describedby`/`aria-invalid`; optional `list` for a `<datalist>`. |
| `AppTextarea` | GovTextarea | `.form-textarea` with the same label/hint/error wiring. |
| `AppSelect` | GovSelect | `.form-select`, options from `items: SelectItem[]` (`{value,text}`). |
| `AppCheckboxes` | GovCheckboxes | `<fieldset>`/`<legend>` + `.form-checkbox` rows from `items: ChoiceItem[]`; Vue-driven conditional reveals via `conditional-<value>` slots; `string[]` model. |
| `AppRadios` | GovRadios | as `AppCheckboxes`, `.form-radio`, scalar model. |
| `AppDateInput` | GovDateInput | **3-field** day/month/year inputs; v-model is an ISO `YYYY-MM-DD` string or `null`. Parse/format logic kept verbatim (no date-picker dependency). |
| `AppBadge` | GovTag | Mosaic pill badge; maps GovTag's `colour` set onto Mosaic `{bg,text}` pairs. |
| `AppPanel` | GovPanel | Violet confirmation panel (title + body slots). |
| `AppDetails` | GovDetails | Native `<details>` disclosure with a violet summary. |
| `AppPopover` | — (new) | Behavioural primitive for the app's dropdown overlays: controlled `v-model:open`, Escape-closes-with-focus-return, outside-mousedown close, one `--z-popover` stacking token. `#trigger` slot (scoped `{ open, toggle, triggerRef }`) + panel default slot; `align` (`left`/`right`/`auto`/`none`) + caller-owned `panelClass`/`panelAttrs`. Anchored **in-flow** (no Teleport), so class-based alignment and the header dropdown's responsive positioning are preserved. Backs `FilterPill`, `DashboardFieldsMenu`, the `JobsView` columns menu, and both `AppHeader` dropdowns; `SearchModal` stays a native `<dialog>` (a modal, not a popover). |
| `ConfirmDialog` | — (new) | Confirmation modal for destructive, irreversible actions. Native `<dialog>` + `showModal()` (same convention as `SearchModal`): focus containment, Escape/backdrop cancel. Parent owns `:open`; props `title`/`message`/`confirmLabel`/`busy`, emits `confirm`/`cancel` (Cancel is focused on open so a stray Enter never fires the destructive default). Drives permanent-delete confirmation in `RecentlyDeletedView` and the detail-view trash banner. |
| `AppBackLink` | GovBackLink | Chevron back link; `<RouterLink>`/`<a>`. |
| `AppBanner` | GovNotificationBanner | `role="alert"` left-border banner; `variant="success"` → green, else info/sky; focuses on mount. |
| `AppErrorSummary` | GovErrorSummary | Red summary card listing `errors: ErrorSummaryItem[]`; **focuses itself on mount** and each link moves focus to its field (a11y preserved). |
| `AppErrorMessage` | GovErrorMessage | Standalone field-error paragraph with a visually-hidden "Error:" prefix. |
| `AppSummaryList` | GovSummaryList | Key/value rows with optional per-row "Change" action links. |
| `AppPagination` | GovPagination | Numeric pagination; props `{ page, totalPages }`, emits `change(page)`. **Still exported from the barrel but no longer mounted** — `DocumentListView` moved to infinite scroll (§1.5). |
| `AppFileUpload` | GovFileUpload | Drop-zone; v-model is `File[] \| null`; `multiple`/`accept` props. Below the zone it lists the **pending selection** (count + name + size per row, `[data-testid="selected-file"]`) with a per-row remove button, so the user can confirm/prune before submitting. In `multiple` mode new picks **accumulate** into the selection (de-duped by name+size+mtime); single mode replaces. Removing the last file resets the model to `null`. |
| — | GovServiceNavigation | **Removed** — its job is now split between `AppSidebar` (nav) and `AppHeader` (search trigger, theme toggle, user menu). |

Two retained custom components, restyled to Mosaic:

- `src/components/SearchModal.vue` — the search-and-filter modal (§1.5).
- `src/components/AppProgressBar.vue` — upload progress bar; violet
  (`bg-violet-500`) fill, `role="progressbar"` with `aria-valuenow`/`aria-label`.

## 1.5 Views and routes

The app's views live in `src/views/`; routes and the auth guard are in
`src/router/index.ts`. Search is **not** a route — it is a modal opened from
several entry points (see "Search modal" below).

| View | Route | Notes |
|------|-------|-------|
| `DocumentListView` | `/` (`documents`) | Dashboard **grid of document tiles** (elevated `.app-doc-card` surfaces — see §1.2); per-tile metadata is driven by the user's saved `dashboardFields` preference, rendered **in the stored order** (the meta row iterates `auth.dashboardFields`, with the ungated "Needs review" badge pinned first — see the card-fields picker in §1.5); `AppBadge` tags; a clamped 3-line **summary excerpt** (`[data-testid="doc-summary"]`, hidden when an active search snippet is shown so the snippet wins); a one-shot flash `AppBanner`. **Infinite scroll** (not numbered pagination): the list *accumulates* — an `@vueuse/core` `useIntersectionObserver` on a foot sentinel appends the next batch (`PAGE_SIZE = 25`) as it scrolls into view, with a visible **Load more** button (the a11y / no-`IntersectionObserver` fallback — jsdom has none) and a **Loading more…** indicator. A filter change resets the list to empty and re-fetches; an in-flight fetch from a superseded filter is discarded via an `AbortController` plus a `generation` guard. A deep-linked `?page=N` loads the first N batches' worth (`limit = N × PAGE_SIZE`) in one go so the link round-trips, then scrolling appends one batch at a time from `offset = items.length`. The whole tile is a click target via a **stretched title link** (`after:absolute after:inset-0` over the `relative` card — a single anchor, no nested links). Tiles with no thumbnail show the file-type label, except PDFs with no thumbnail (unrenderable, usually password-protected) which show a **padlock placeholder** (`isLockedPdf`). **Any text document** (`text/*` — both `text/plain` and `text/markdown`, e.g. email bodies, notes, plain-text uploads) that has metadata instead renders a **metadata "facsimile"** in the preview area (`[data-testid="markdown-preview"]`) — built purely from existing list-item fields: the **title** as a heading line (`text-base`), then one line each (`text-sm`) for **Kind, From (sender), To (recipient), Date (`document_date`)**, with empty fields omitted — sized up for readability — rather than the generic "Text" placeholder; a text document with no metadata still shows "Text". All search/filter state lives in the URL query. **Live status:** watches `jobsStore.lastEvent` and patches a tile's `status` (its Processing/Failed badge) **in place** as that document advances — no refetch, so scroll position and accumulated pages are preserved. |
| `DocumentDetailView` | `/documents/:id` (`document-detail`) | Directly below the **Back to documents** link and above the hero sits a **previous/next document** nav (`[data-testid="doc-neighbors"]`, links `doc-prev`/`doc-next` → the neighbouring `document-detail` routes). Neighbours are computed by the **`useDocumentNeighbors`** composable (`src/composables/useDocumentNeighbors.ts`) and navigate **by document id** — Next → next-higher id (N+1), Previous → next-lower id (N-1), independent of the list sort (stepping in id order reads as intuitive; following the default newest-first sort sent "Next" to an *older*, lower id). There is no server neighbour endpoint and no `id` sort, so it scans `GET /api/documents` **unfiltered** by `added_date desc` (effectively id-descending, since `created_at` and the autoincrement id are both set at insert), paginating (100/page, capped at 20 pages) and reading the nearest ids either side of the current one **numerically** — correct even if two documents tie on `added_date`. It is self-contained (survives a cold deep-link), degrades to no-neighbours on a fetch error, and hides a direction at the ends of the list. The whole bar is hidden while in **review-queue** mode (the queue bar owns navigation there) and for **trashed** documents (excluded from the list, so they have no neighbours). Leads with a full-width **hero header card**: the title (`h1#document-title`) in a **flex row** with an **"Ask about this document"** button (`[data-testid="ask-about-document"]`) — a **primary (violet) `AppButton`** with a chat icon that sits **top-right of the title row on desktop** and **stacks under the title on mobile**. That button opens the Ask view in a **new tab** (`target="_blank"`) at `{ name: 'ask', query: { q: <prompt> } }` — `AppButton` gained a `target` prop that passes through to its `RouterLink`/anchor form and auto-adds `rel="noopener"` for `_blank`. The pre-filled prompt reads `Tell me about the document "<title>" (<kind> from <sender>, <date>): ` — any missing kind/sender/date is gracefully omitted (no empty parentheses), and the title falls back to `original_filename` then a generic `this document`. This is **pre-fill only**: there is no backend change and no document-scoped retrieval — the prompt just names the document so the existing Ask RAG surfaces it (see [ask.md §1.2](ask.md)). Both the hero button and the floating **Action dock** (below) render as real `target="_blank"` anchors sharing one `askHref` computed (`router.resolve({ name: 'ask', query: { q: askPrompt } }).href`), so native new-tab affordances (middle-click, cmd/ctrl-click, "open in new tab") work on either. Once the hero has scrolled off screen — tracked by an `IntersectionObserver` on `#document-hero` — the **`ActionDock.vue`** component mounts (extracted from this view and renamed from the earlier inline "island"): a `sticky` (not `fixed`) full-content-width wrapper (`[data-testid="action-dock-wrapper"]`, `v-if` on the whole component, not `v-show`, so it is fully absent from the DOM while the hero is visible) keeps it inside the page's own scroll container rather than floating over the sidebar, and holds a pill (`[data-testid="action-dock"]`) with the hero's two primary actions: an **Ask** anchor (`[data-testid="action-dock-ask"]`, the same `askHref`/new-tab anchor as the hero button) and an **Edit/Done** toggle (`[data-testid="action-dock-edit-toggle"]`, `aria-pressed`) that flips the *metadata* edit mode. The dock's on-screen position is a per-user preference — one of `top-left` / `top-middle` / `top-right` (default) / `bottom-left` / `bottom-right` — read from `dockPosition` on the auth store and set via **Settings → Appearance → Action dock position** (`PUT /api/settings/appearance`; see [api.md §1.10.3](api.md)); the `top-*` positions carry a `top-16` offset so the dock clears the sticky, fixed-height (`h-16`) `AppHeader` instead of rendering underneath it. The pill row is then inset from that edge (`top-4` / `bottom-4`) so it floats with a comfortable gap rather than squished flush against the header / viewport bottom, and its horizontal padding matches the navbar (`px-4 sm:px-6 lg:px-8`) so a left/right-anchored dock lines up with the header's outermost elements (which span the full content width) rather than the narrower, max-width-capped main column. That edit-mode flag is lifted into a shared singleton composable, **`useMetadataEditMode`** (`src/composables/useMetadataEditMode.ts`, a module-level ref mirroring `useDocumentLayout`'s `editMode` — not persisted, so a reload/navigation never resumes with the editors open); the hero's `[data-testid="edit-toggle"]`, every metadata section tile, and the dock's toggle all read/flip the one flag, so opening the editors from the dock shows exactly the same per-field editors the hero toggle would. This metadata edit mode is **distinct** from **Edit layout** (`useDocumentLayout`, below) — the dock only surfaces the metadata toggle, not layout editing. The hero also carries a labelled **stat row** (Kind · Sender · **Recipient** · Date on document · **Date added to library** · **Last edited** · Amount, plus opt-in Language / Due date / Expiry date hidden by default) rendered from the per-machine `useDocumentLayout` field list (see the **Edit layout** mode in the component-structure section below); in **read mode it shows only stats that are both *visible* in the saved layout and *populated*** (no em-dash filler), while the three dates read as a distinct trio — **Date on document** (the date printed on the document, editable), **Date added to library** (`created_at`, read-only) and **Last edited** (`updated_at`, read-only, bumps on any change incl. tags/projects) — the last two rendered with date+time, the document's **tags as colour-varied `AppBadge` pills** (colour derived from the tag name via `tagColour`, so it's stable across renders), and its **projects as `AppBadge` pills wrapped in `RouterLink`s** (`[data-testid="project-badge"]`, each → `/?project=<slug>` to filter the dashboard to that project). Below the hero, **two columns on desktop**: the **metadata tiles on the left** and the **preview pane on the right**. What used to be one "Details" card is now **one first-class tile per metadata section** — Content (which also holds **Kind + Language**, the former standalone "Classification" tile, since folded in — a two-field panel read as over-fragmented), Sender, recipient & dates, Financial, and a read-only System tile — each rendered from the same `DocumentMetadataEditor.vue` with a `section` prop (its group from `fieldGroups` + `ACCENT`), so each carries an accent-coloured heading and is **independently drag-reorderable** across columns like any other section card. A **value-less tile hides entirely in read mode** (Content and System always show; Sender, recipient & dates / Financial appear only when populated), so a born-digital note shows no empty "Financial"/"Sender, recipient & dates" card at all; **entering edit mode reveals every tile** so anything stays fillable, and within a *present* tile a value-less field still renders with an em-dash. **Topics** fold into the **Content** tile (they describe the document's "aboutness"), not a tile of their own. Fields lay out in a **two-column grid** (`sm:grid-cols-2`; long fields span both columns, and in edit mode every field spans both) with **larger values** (`text-base`) under **smaller uppercase labels** (`text-xs`); Amount renders as an emphasised figure and Status as a coloured pill (`statusAccent`). Editing is a single **page-wide Edit toggle in the hero** (`[data-testid="edit-toggle"]`, label **Edit details**/**Done**, `aria-pressed`, kept visually distinct from the neighbouring **Edit layout** toggle) that flips the shared `useMetadataEditMode` flag **every section tile reads** — *not* a per-tile toggle and *not* the old per-row "Change" buttons. Toggling on reveals an inline `App*` editor for **every** field at once (with a "changes save automatically as you leave each field" hint); each field **autosaves independently** the moment its edit commits (native `change` bubbles from the input to the field wrapper; text fields also commit on Enter), PATCHing **only that field** via the existing per-field endpoint. There is **no global Save/Cancel** — "Done" just leaves edit mode. A dirty-check guards against needless PATCHes, a server-canonicalised value (e.g. slugified tags) is re-synced back into the editor, a brief green **"Saved"** indicator appears per field (`[data-testid="saved-<field>"]`), and validation/save errors show **inline per field** (`fieldError[field]`). Rows keep their `row-<field>`/`row-value` hooks; the **Tags** row (`#edit-tags`, in the Content group) is a **token multiselect** (`AppMultiSelect`) over tag **slugs** — selected tags show as removable chips, an input filters existing tags (from the shared taxonomy cache) into a menu, and typing a slug that matches nothing offers a **"Create …"** option; every add/remove autosaves the full-replacement slug list and refreshes the taxonomy cache, and read-mode tags render as `AppBadge` chips linking to the tag-filtered dashboard (`[data-testid="tag-badge"]`, `/?tag=<slug>`). The **Projects** row (`#edit-projects`, in the Content group) is a **token multiselect** (`AppMultiSelect`): selected projects show as removable chips, an input filters existing projects (from the shared taxonomy cache) into a menu, and typing a name that matches nothing offers a **"Create …"** option — every add/remove autosaves the full-replacement `projects` list via PATCH (unknown names upserted server-side), then refreshes the taxonomy cache so a newly created project is offered everywhere. Within the **Sender, recipient & dates** group, the **Recipient** field — like Kind — is an `AppSelect` dropdown ("Not set" + every known recipient, options from the shared taxonomy cache via `GET /api/recipients`) with an inline **"Add recipient…"** affordance (a sentinel option that reveals a text input to name a brand-new recipient without leaving the page); both paths PATCH `{recipient: <name>|null}`, upserted case-insensitively server-side, and after an inline add the dropdown options reload so the new name appears. The System group's **OCR confidence** (`[data-testid="ocr-confidence"]`) shows the engine score as a percentage, or — when null — distinguishes provenance: **"Imported (Paperless) — text layer reused from Paperless — no OCR re-run"** when `source === 'import'`, otherwise **"Not applicable — born-digital text — no OCR run"** (born-digital PDFs and plain-text uploads skip OCR, so no confidence is recorded — see [ingestion.md](ingestion.md)). The refined per-page **markdown** is a **first-class reader card** ("Document text", `[data-testid="markdown-content"]`) **eagerly fetched on load** from `GET /api/documents/{id}/markdown` (no longer a collapsed `View markdown` disclosure): pages render continuously with `marked` + `DOMPurify`, a "Page N" separator only when `page_count > 1`. For a born-digital `.md`/`.txt` with no PDF/image, this reader **is** the primary pane — the "No preview is available" fallback is suppressed when readable text exists (`hasReadableText`). The raw `ocr_text` is not surfaced in the UI (it still backs full-text search). When the document belongs to a recurring **series** (same sender + kind), a **`DocumentSeriesTrend`** panel (`v-if="doc"`) renders below: it fetches `GET /api/documents/{id}/series` on mount and, when `status:"ok"`, delegates to the shared **`SeriesChartTile`** (standard tile chrome) with `highlightDocumentId` set to this document. It now carries its **own `ChartControls` bar** (`[data-testid="doc-series-controls"]`) — the same time-range / from / to / **Group by** controls as the `/charts` pages, with the **same defaults** (time range **Last 12 months**, grouping **month**) but **independent** per-machine storage keys (`library:doc-series-timeframe` / `-custom-from` / `-custom-to`, `library:doc-series-grouping`) so adjusting the trend here doesn't move the charts dashboard. Timeframe filtering is applied to the points client-side (the `/api/documents/{id}/series` endpoint returns the full series); an empty window renders a graceful empty-state (`[data-testid="doc-series-empty"]`) with the controls still visible. The tile draws a **Chart.js bar chart** of the series' dated amounts with **this document's point highlighted** (`#dc2626`, matched by `document_id`, others `#2563eb`), the cached **LLM description** (`[data-testid="series-description"]`, when present), a one-line verdict (e.g. *"6.4% above usual · trend rising"*) + cadence, and a list of **citation links** (`[data-testid="series-citation"]`, each point → `/documents/{id}`, labelled by the point's title). The chart bars are themselves **click-through to the source document**: in **ungrouped** mode clicking a bar navigates to that point's `/documents/{id}` (cursor turns to a pointer on hover; the click `stopPropagation`s so a bar click opens the *document*, not the chart page); in **grouped** mode — where one bar aggregates several documents — the canvas tooltip is replaced by a **sticky HTML tooltip** (`[data-testid="chart-tooltip"]`) that stays open while hovered and lists each contributing document as a **clickable link** (`[data-testid="chart-tooltip-doc-link"]` → `/documents/{id}`). This works everywhere `SeriesChartTile` is used (the charts grid, the single-chart page, and the document-detail trend). Here the tile is **editable** (`editable` prop): each document carries a **remove** control (`[data-testid="series-remove"]`) and a **`+ Add document`** toggle (`[data-testid="series-add-toggle"]`) revealing a search-to-pick box (`[data-testid="series-add-search"]` → `listDocuments`) whose results pin the chosen document into the series; add/remove call `POST`/`DELETE /api/series/{sender_id}/{kind_id}/members` (W8) and the wrapper **refetches** on the `changed` event. The wrapper **self-hides** (renders nothing) when the series is `insufficient` or the fetch errors, so the page is unchanged for documents without a qualifying series. See [ask.md §1.7](ask.md). The preview card has a slim **header bar** with **Open** (new tab → inline URL) and **Download** (attachment → searchable PDF when present, else original) buttons, so the document window itself stays chrome-free. PDF rendering is handled by **`DocumentPdfPreview.vue`** (`pdfjs-dist`): pdf.js decodes the file in a Vite-bundled worker and renders every page to a `<canvas>`, scaled to fit the container width (`devicePixelRatio`-aware). Pages load lazily via `IntersectionObserver` (300 px root-margin look-ahead), so only visible pages are rendered; a faded first-page thumbnail (`poster` prop, when a thumbnail exists) is shown while loading. This produces identical output across Chrome, Firefox, and Safari — there is no native viewer chrome, no per-engine toolbar quirk, and no UA-sniffing. Three fallback states: **loading** (spinner + optional poster), **password** (padlock icon + Open link), and **error** (Open + Download links). Stacks on mobile, preview first; both columns are `min-w-0` and text containers `break-words` so long titles/values wrap rather than widen the page (which made iOS Safari zoom in). **Deleted (trash) documents:** the view fetches with `getDocument(id, { includeDeleted: true })`, so a title click from **Recently Deleted** opens a soft-deleted document **read-only** instead of 404ing. When the loaded document has `deleted_at` set, a red **trash banner** (`[data-testid="trash-banner"]`) renders above the hero with **Restore** (`[data-testid="trash-restore"]` → `POST .../restore`, clears the banner) and **Delete permanently** (`[data-testid="trash-purge"]`, opens a `ConfirmDialog` → `DELETE .../permanent`, then routes back to `/deleted`); the ordinary soft-delete link is hidden while deleted. **Live status:** watches `jobsStore.lastEvent` and, on an event for *this* document, refetches it (`getDocument`) so the Status pill and any pipeline-filled metadata refresh without a reload — suppressed while a re-extraction poll is running, which owns refreshes then. A **Comments** card (`DocumentComments.vue`, `[data-testid="document-comments"]`) sits in the metadata column, by default after the metadata section tiles and before Actions: an "Add a comment" `AppTextarea` + submit button, then a newest-first list of existing comments (each showing its dated `created_at` timestamp — a comment's recorded date, not the document's own date — plus per-comment **Edit**/**Delete** controls, `[data-testid="comment-item-{id}"]`/`comment-edit-{id}`/`comment-delete-{id}`); at most one comment is in edit mode at a time (singleton `comment-edit-body`/`comment-edit-save`/`comment-edit-cancel` testids, mirroring `NoteEditorPanel`'s pattern). A comment is a NEW concept, distinct from a note: a note is its own `source='note'` Document, while a comment is user-authored dated text attached to an *existing* document via `POST`/`PATCH`/`DELETE /api/documents/{id}/comments[/{cid}]` ([api.md §1.19](api.md)); every add/edit/delete re-fetches the parent document (`@changed`) and queues a re-embed so `/ask` can find the document through its comments. The card is registered as card id `'comments'` in `useDocumentLayout`'s `DEFAULT_CARD_COLUMNS` (in the left/metadata column by default; see the component-structure section below), so it participates in **Edit layout** show/hide + free-form drag like any other section card. The Actions card has a **View job history** link (`[data-testid="view-job-history"]`) → `/jobs?document_id=<id>` (the "Ask about this document" button now lives in the hero, above). Below it, a **History** card (`DocumentHistoryTimeline`, `[data-testid="document-history"]`) renders the document's `events` audit trail as a **reverse-chronological** timeline of **humanized milestones** (newest first — the most recent event sits at the top; equal timestamps keep their incoming order via a stable sort) (Ingested, OCR complete, Description & metadata added, Indexed for search, Edited, Projects changed, …). It is meant to be the **self-sufficient record of how the document was processed**, so processing-relevant steps carry a small breakdown rather than a bare label: the **extraction** milestone (`extraction_completed`) shows a **method sentence** describing how the input was sent — distinguishing normal OCR-text extraction, the "OCR was unusable → original file sent" case, the **model-only escalation**, and, given a **violet-accented** unmissable line, the **vision fallback** (`[data-testid="history-extraction-method"]`, when the low-confidence retry re-read the original file — `escalated` + `input_mode` ∈ {document, image}) — plus a wrapped row of small labelled **chips** (`[data-testid="history-extraction-chip"]`: model, confidence, and cost). An **`extraction_skipped`** step is now surfaced as its own milestone (labelled "Extraction skipped") with its reason (budget skips show spent-of-budget; input/file skips show the detail string), and **failure** steps (`extraction_failed` / `ocr_failed` / `markdown_failed` / `embedding_failed`) surface their carried error message (`[data-testid="history-secondary"]`). The noisy per-stage `status_changed` transitions and the low-signal `*_skipped` events (`embedding_skipped`, …) remain hidden by default; only `extraction_skipped` graduates to a milestone. A **"Show all events"** disclosure (`[data-testid="history-show-all"]`) still reveals the complete raw log (including raw token counts, which stay out of the curated view). **Topics** (the auto-extracted subject phrases) render as **read-only** colour-varied `AppBadge` pills (`[data-testid="topic-badge"]`) inside the **Content tile** (below its fields, shown whenever non-empty) — there is **no topics editor** (topics are owned by extraction and indexed for search; see [api.md §1.5](api.md)); only `tags` remain editable. **Notes** (`source === 'note'`) get a dedicated surface instead of the generic per-field editor: a page-wide note **Edit** toggle reveals a markdown-body draft (no separate title field — the title is the body's first line via `deriveNoteTitle`) with the **same edit / split / preview view-mode toggle** as the note-create view (shared via the `useMarkdownEditorMode` composable and the `library:note-editor-mode` storage key, so the preference is global) and a live sanitised preview pane (`[data-testid="note-edit-preview"]`), that `PATCH /api/notes/{id}` in place (`updateNote`, sending `{title: deriveNoteTitle(body), body_markdown}`), and a collapsible **version-history** panel (`listNoteVersions`) lists each snapshot with a per-version **Restore** action (`restoreNoteVersion`) — see [api.md §1.17](api.md). |
| `DocumentDeleteView` | `/documents/:id/delete` (`document-delete`) | A confirmation page (its own URL, not a JS modal) with a destructive `AppButton` + `AppBackLink` cancel. |
| `RecentlyDeletedView` | `/deleted` (`documents-deleted`) | The **Recently Deleted** holding area: an `.app-doc-grid` mosaic of soft-deleted documents (`GET /api/documents/deleted`), each tile (`[data-testid="doc-card"]`) showing the title (→ detail), kind·sender, the deleted date, and a countdown (`[data-testid="purge-countdown"]`, "Purges in N days" / "Purges soon" at 0). A per-tile **Restore** button (`[data-testid="restore-<id>"]`) calls `POST /api/documents/{id}/restore`, removes the card, and shows a success `AppBanner` (`[data-testid="flash-banner"]`). A per-tile **Delete permanently** button (`[data-testid="purge-<id>"]`) opens a `ConfirmDialog`; confirming calls `DELETE /api/documents/{id}/permanent`, removes the card, and flashes. The title links to the detail route, which opens the deleted document **read-only** (the detail view fetches with `?include_deleted=true` and shows a trash banner) rather than 404ing. Loading / error / empty (`[data-testid="deleted-empty"]`) states; an intro line names the `retention_days` window. Reached from the sidebar **Recently Deleted** link (`[data-testid="sidebar-deleted-link"]`). See [api.md §1.6](api.md). |
| `UploadView` | `/upload` (`upload`) | `AppFileUpload` drop-zone (`accept` covers images, PDF, and **text/markdown notes** — `.md`/`.markdown`/`.txt`); each file uploads independently with its own `AppProgressBar`, then polls until `indexed`/`failed`; duplicate/error states via `AppBanner`/`AppErrorSummary`. |
| `NewNoteView` | `/notes/new` (`note-new`) | In-app **note authoring**: a `#new-note-form` with an `AppTextarea` markdown body (`#note-body`) and a **live preview** (`[data-testid="note-preview"]`) rendered through the same `marked` + **DOMPurify** sanitise pipeline as the detail-view reader. The "first line becomes the title; Markdown supported" guidance lives in the **`PageHeader` description** (not a hint above the editor), so the edit and preview panes start at the same top edge and stay vertically aligned. An **edit / split / preview** view-mode toggle (Split is the default; the wide-only Split button hides on narrow screens) controls which panes show — sourced from the shared **`useMarkdownEditorMode`** composable (`src/composables/useMarkdownEditorMode.ts`) and persisted per-machine under the `library:note-editor-mode` storage key, so the same preference drives the in-place note editor in `DocumentDetailView` (a display-size preference — [frontend-view-principles.md](frontend-view-principles.md) §4). There is **no separate title field** — the **title is the first line of the body** (`deriveNoteTitle` in `src/utils/noteTitle.ts`: first non-empty line, leading markdown heading marker stripped, capped at 200 chars). Save (`#note-save`, disabled until that first line is non-empty) `POST`s `{title: deriveNoteTitle(body), body_markdown}` to `/api/notes` (`src/api/notes.ts`) and routes to the new note's `document-detail`; API failures surface in `AppErrorSummary`. Reached from the sidebar **New note** link (`data-testid="sidebar-notes-link"`). |
| `AskView` | `/ask` (`ask`), `/ask/:threadId` (`ask-thread`) | **SRE-Agent-style chat layout** (W10): no `max-w-*` cap (the shell owns width). The shared **`PageHeader`** (title + description) sits **full width at the top**; below it the **working area** (`#ask-page`) holds the **`ConversationSidebar`** (thread search `[data-testid="thread-search"]`, "New conversation" button — greyed out and disabled when the view is already an empty new conversation — and an internally-scrolling thread list with a subtle `.thin-scrollbar`; each thread row (auto-named — see [ask.md §1.6](ask.md)) carries inline **Rename** (`[data-testid="thread-rename"]` → a title input seeded with the current name + Save/Cancel, Enter saves / Esc cancels, blank-or-unchanged is a no-op) and two-step **Delete** affordances) and the answer column. **Below `lg` the working area STACKS** (`flex-col`): the sidebar is full-width directly under the title/description (its list capped `max-lg:max-h-72`), with the transcript + composer beneath — no cramped side-by-side column on a phone. **On `lg+`** it is a two-pane row (a 256px sidebar beside the answer column) that **grows with the conversation**: the panel is *not* pinned to the viewport height — the **whole page scrolls**. The `[data-testid="ask-transcript"]` flows at its natural height (the empty state keeps a `min-h-[18rem]` floor so the panel never looks broken before the first turn), and the composer is a `shrink-0` flex sibling that flows beneath the thread and **scrolls with the page** (not `position:sticky`, so it can't float over the transcript and intercept citation clicks — see `e2e/ask-page-citation.spec.ts`). After a new turn, `scrollToBottom` brings the latest turn into view via the page scroll (`scrollIntoView`) rather than a transcript-internal jump. Each turn is **visually layered** so the panel, the question, and the answer are distinguishable: the question is a **right-aligned violet chat bubble**, and the answer sits on a **subtle surface card** (`[data-testid="ask-answer-surface"]`, `bg-gray-50 dark:bg-gray-900/40` + hairline border) — the sanitized markdown answer (`#ask-answer`, `marked` + `DOMPurify`, GFM **tables** styled), the citations **collapsed by default** behind an `AppDetails` disclosure ("Citations (N)") that opens to a 2-col grid of citation cards (each a `RouterLink` to `document-detail`), and a subtle `used_tools`/`cost_usd` meta line. The composer (`[data-testid="ask-form"]`) holds a multi-line `AppTextarea` `#ask-question` (hint: "Enter to send · Shift+Enter for new line") whose `@keydown` handler (`onComposerKeydown`) decides what each key does: plain **Enter sends**; **Shift+Enter** and **Ctrl+J** insert a newline at the caret instead (Ctrl+J is `preventDefault`ed and spliced in manually, since it has no default insertion behaviour); **Cmd/Ctrl+Enter** still sends (it falls through the same "Enter, not Shift" branch); and Enter is ignored entirely while an IME composition is in progress (`event.isComposing` or the legacy `keyCode === 229`), so committing a candidate never sends prematurely. An **Attach image** control (`[data-testid="ask-image-attach"]` → hidden `[data-testid="ask-image-input"]`, up to 5 images previewed as thumbnails `[data-testid="ask-image-preview"]` with a remove button, base64-encoded client-side — W11), and a right-aligned **Send** `AppButton` `#ask-submit` (which becomes a live **Stop** button while an answer is generating) → `POST /api/ask` (`src/api/ask.ts`), posting the question + any `images` with the active `thread_id`. An empty state shows when no turn is on screen, and distinguishes two cases: when **no conversations exist yet** it invites a first question (`[data-testid="ask-empty"]`); when **conversations exist but none is selected** it prompts the user to pick one from the sidebar or start a new one (`[data-testid="ask-select-thread"]`). Errors (notably the 503 "no API key" case) surface in `AppErrorSummary`. A **successful answer never shows an error**: the post-success side effects (record `thread_id`, sync the URL to `/ask/:threadId`, refresh the sidebar) run in `syncThread` *outside* the answer-error `catch`, and a missing/non-numeric `thread_id` or a Vue Router navigation rejection is logged rather than turned into a spurious "Something went wrong" alert. See [ask.md](ask.md). |
| `SettingsView` | `/settings` (`settings`) | Tabbed settings (`role="tablist"`). **Dashboard** tab: the shared **`DashboardFieldsEditor`** (checkbox per field **plus drag/Up-Down reorder** and "Reset to defaults" — the same component the dashboard "Fields" popover uses, see §1.5), with an explicit **Save changes** → `PUT /api/settings`. **Appearance** tab: page-canvas tone swatches (`BACKGROUND_TONES`) **and** a document-tile preview choice (`TILE_PREVIEWS`: full-width top crop, the default, vs whole-page letterbox), both applying live and auto-saving per click → `PUT /api/settings/appearance` (optimistic store update so the canvas/tiles repaint instantly; reverts on failure). The same tab also has an **Action dock position** card (`[data-testid="settings-dock-position"]`, `role="radiogroup"`): five buttons — `dock-position-top-left` / `dock-position-top-middle` / `dock-position-top-right` (default) / `dock-position-bottom-left` / `dock-position-bottom-right` — choosing where the document-detail page's floating **Action dock** (§1.5, `DocumentDetailView` component structure) appears; picking one auto-saves the same way, optimistically updating `auth.dockPosition` → `PUT /api/settings/appearance`. A **Phone columns** card (`[data-testid="settings-phone-columns"]`, `role="radiogroup"`, buttons `phone-columns-1`/`phone-columns-2`/`phone-columns-3`) sets how many dashboard tile columns render on phone-width screens (`< 641px`; default **2**, was 1 before this preference existed — existing users' phones flip to 2 columns) — a server-synced account preference (`phone_columns` on `auth.phoneColumns`, stored in the `user.preferences` JSON blob, no DB migration) driving the `--doc-grid-cols-phone` CSS var read by `.app-doc-grid` (§1.4); the tablet band stays fixed at 2 and the desktop `--doc-grid-cols` "tiles per row" control (a separate, `localStorage`-only preference) is unaffected. Picking a count auto-saves the same optimistic way → `PUT /api/settings/appearance`. Also a **Document type colours** card: one row per kind (loaded via `GET /api/kinds`, ordered most-used first) with a native colour picker, one-click `SUGGESTED_COLORS` swatches, a per-kind **Default** reset and a **Reset all**, saving the sparse override map → `PUT /api/settings/kind-colors` (optimistic; `resolveKindColor` in `utils/kindColor.ts` resolves override → `DEFAULT_KIND_COLORS` → neutral). All three surface success/error via `AppBanner`/`AppErrorSummary`. **Email triage** tab (`[data-testid="tab-email-triage"]`): a **read-only** view of the instance-wide email-in triage pipeline — fetched **lazily on the tab's first show** from `GET /api/settings/email-triage` ([api.md §1.10.6](api.md)). Shows a **Hold pipeline ON/OFF** badge (`email-triage-hold-master`), a "view held emails" link to `/held-emails`, the poll interval / Held & Processed folders / IMAP timeout, and the five-step decision flow as an ordered list — sender allowlist (accept-all vs N allowed senders + unknown-sender hold badge), noise gate (+ tiny-image thresholds and the decoration-image signal ceilings, `triage-decoration-thresholds`), LLM verdict (Active / Inactive — distinguishing "no API key" from "disabled by configuration"; model, daily budget, prompt version, and the fail-open note), body substance gate (word/char thresholds + below-substance hold badge), and the nothing-ingested hold — each step with live values and an `AppBadge` where a switch exists. Below the flow, a **"Recently skipped items"** card (`triage-recent-skips`, fed by `GET /api/settings/email-triage/recent-skips` [api.md §1.10.7](api.md), loaded alongside the config): the last 20 emails with a filtered/dropped item, each row showing subject/sender/time plus its per-item skip reasons — the first place to look when a forwarded attachment seems to have vanished. Its load is best-effort: a failure shows an unavailable note (`triage-recent-skips-error`) instead of a false "no skips", and never blanks the config view. Nothing is editable (settings are environment-only; the footnote cites `docs/runbooks/email-triage.md`); when `email_in_configured` is false a single "Email-in is not configured on this server" empty state (`email-triage-unconfigured`) replaces the flow. Semantics: [ingestion.md](ingestion.md), "Email item selection" / "Held for review". |
| `ChartsView` | `/charts` (`charts`) | Aggregate **charts dashboard**: a responsive grid (`grid-cols-1 lg:grid-cols-2`, `[data-testid="charts-grid"]`) of **`SeriesChartTile`**, one per eligible recurring `(sender, kind)` series, fed by `GET /api/charts`. Each tile shows the bar-chart trend, the cached LLM description, and an **editable** list of its documents (no per-document highlight here — the latest member is highlighted): tiles render with the `editable` prop, so each document has a **remove** control and a **`+ Add document`** search-to-pick that pins a chosen document into the series (`POST`/`DELETE /api/series/{sender_id}/{kind_id}/members`, W8); a `changed` event refetches the whole grid (`@changed="load"`). Each tile also carries an **inline title/description editor** (`[data-testid="series-meta-edit"]` → title input + description textarea → `PUT /api/charts/{seriesId}/meta`, W12; an override title is preferred over the derived `sender · cadence series` heading) and a **deep link** to its own page (`[data-testid="series-detail-link"]` → `/charts/{seriesId}`) when given the `detail-link` prop. Loading (`charts-loading`), empty (`charts-empty`, "no recurring series yet"), and error (`charts-error`) states. The view also hosts the **Create a new series** flow (W14, authored/manual series): a `[data-testid="charts-create-button"]` reveals an inline form (`[data-testid="charts-create-form"]`) — a **name** (`charts-create-name`), a **currency dropdown** (`CurrencySelect` under `charts-create-currency`: built-in EUR/GBP/USD plus an inline "Add another…" that appends a custom 3-letter code persisted per-machine via `useCurrencyOptions` / `library:currency-options`), an optional **subtitle/context** textarea (`charts-create-description`, sent as `description`), and a **document search-to-add** (`charts-create-search` → `listDocuments`, results `charts-create-result`, chosen docs shown as removable chips `charts-create-selected`) — that `POST /api/charts/authored` (`createAuthoredSeries`) and reloads the grid. A **mechanical currency-mismatch warning** (`charts-create-currency-warning`) appears when a selected document's own currency differs from the chosen series currency (advisory only; no LLM, creation not blocked). A **candidates ("almost there")** affordance surfaces emergent buckets one document short of charting: when `GET /api/charts` returns any `candidates` (buckets with `2 ≤ count < min`), a header toggle (`[data-testid="charts-candidates-toggle"]`, showing the visible count) reveals an opt-in panel (`[data-testid="charts-candidates"]`) of rows (`charts-candidate`, "`sender · kind` — N of M documents"); each row's **Create chart** button (`charts-candidate-promote`) promotes the bucket into an authored series seeded with its `document_ids` (`createAuthoredSeries` with a derived `sender · kind` name) and reloads the grid. The backend excludes any bucket already backed by an authored series (matched on that series' signature), so the reload after a promote simply drops the row — no client-side dedup needed, and the guard survives a page refresh (preventing a duplicate promote). A per-row in-flight guard (`promotingKey`) disables the button during the promote; errors surface in `charts-candidates-error`. A view-level **shared control bar** (`ChartControls`, `[data-testid="chart-controls"]`) drives every tile: a **time range** select (`charts-timeframe`, `useChartsTimeframe` / `library:charts-timeframe-v2`: All / Year to date / Last quarter / **Last 12 months** *(default)* / Last 3 years / Custom range) that clamps each tile's x-axis to the same window (display-only, passed as `axis-min`/`axis-max`); two **datepickers** (`charts-range-from` / `charts-range-to`, `AppDateInput`) that always reflect the active window — picking a preset fills them, editing one flips the selection to Custom (`library:charts-custom-from`/`-to`); and a **Group by** select (`charts-grouping`, `useChartsGrouping` / `library:charts-grouping-v2`: No grouping / week / **month** *(default)* / quarter / year) that buckets each series' documents into calendar periods and **sums** the amounts into one bar per period (`groupSeriesPoints`, client-side, passed as the `grouping` prop). The `-v2` keys reset everyone to the new defaults once; a later manual choice still persists. In grouped mode a bar's **tooltip** shows the period **total + document count** on the first line, then **each contributing document's amount on its own row** (capped ~12, "+N more"); the red bar is the highlighted latest document and appears only when ungrouped. Tiles are **click-to-open** (W3): the heading (`series-heading-link`) and the whole chart area (`series-chart-area`, `role="link"`) navigate to the single-chart page. Authored tiles also carry a **Delete** affordance (`series-delete` → inline confirm `series-delete-confirm-button` → `DELETE /api/charts/authored/{id}`, `deleteAuthoredSeries`; emergent series can't be deleted, having no stored row); a `deleted` event drops the tile from the grid without a refetch. Authored series render as ordinary tiles alongside the emergent ones; their per-tile key + deep link use the `a-{id}` scheme via `authoredSeriesId()`, and editing an authored tile's title/description PATCHes the authored row (`updateAuthoredSeries`) rather than the meta-override endpoint (the tile branches on `authored_id`). **Smart features on authored tiles** (driven by the additive `signature`/`suggestion_count`/`odd_one_out_count` keys on the series body): a violet **suggestions** panel (`series-suggestions`, lazily loaded on expand) proposes signature-matching documents with per-row **Add** (`acceptAuthoredSuggestion`) / **Dismiss** (`dismissAuthoredSuggestion`); an amber **odd-ones-out** panel (`series-odd-ones-out`, lazily loaded on expand) lists members that break the signature with their **deterministic, grounded** `reason` (built server-side from the documents' real sender/kind/currency — no LLM, so it can never invent a value absent from the series) and a **Remove** action. Reachable from the sidebar **Charts** link (`[data-testid="sidebar-charts-link"]`). See [ask.md §1.7](ask.md) and [api.md §1.14.3](api.md). |
| `SeriesChartView` | `/charts/:seriesId` (`series-chart`) | **Full-screen, shareable chart** for one series: fetches `GET /api/charts/{seriesId}` and renders one **editable**, **large** (`size="large"`, taller chart) `SeriesChartTile` at full content width (no `max-w-2xl` box). The `seriesId` is either the stable emergent `{sender_id}-{kind_id}-{currency|none}` id (`seriesId()`) or an authored `a-{id}` id (`authoredSeriesId()`); both resolve in `src/api/documents.ts`. A **toolbar** above the chart hosts the same shared `ChartControls` (time range + custom datepickers + grouping, persisted with the grid) plus **export/share actions** (`chart-export`): **Download PDF** (`chart-export-pdf`, `jspdf`), **JPEG** (`chart-export-jpeg`) and **PNG** (`chart-export-png`) via the Chart.js canvas (`chartExport.ts`; the tile exposes `getChartCanvas()`), and **Copy link** (`chart-share`, `navigator.clipboard`). Deleting the series (`series-delete`) routes back to `/charts`. Loading (`series-chart-loading`) and not-found (`series-chart-error`, on a 404 / unknown series) states, plus a **← All charts** back link (`[data-testid="series-chart-back"]`). |
| `ProjectsListView` | `/projects` (`projects`) | **Projects index**: lists every project (`GET /api/projects`) as a card (`[data-testid="project-row-<slug>"]`) with its **document count** (`project-count-<slug>`) and a **name link** (`project-link-<slug>` → `/?project=<slug>`, the project-filtered dashboard); archived projects show an **Archived** badge. A **Show archived** toggle (`project-archived-toggle`) re-fetches with `?include_archived`. **Admins** additionally get management controls (all backed by the admin-only projects endpoints, refreshing the shared taxonomy cache after each mutation): a **+ New project** form (`project-new-button` → `project-create-form` with name + optional description → `POST /api/projects`), per-row **Edit** (inline rename + description → `PATCH`), **Archive/Unarchive** (`project-archive-<slug>` → `PATCH {archived}`), and a **two-step Delete** (`project-delete-<slug>` reveals `project-delete-confirm-<slug>` → `DELETE`; no blocking dialog). Non-admins get the read-only list. Reachable from the sidebar **Projects** link (`[data-testid="sidebar-projects-link"]`). See [api.md §1.16](api.md). |
| `SavedViewsView` | `/saved-views` (`saved-views`) | **Manage saved views**: lists the caller's saved views (`GET /api/saved-views`, via the `savedViews` store) as rows (`[data-testid="saved-view-row"]`) with **Apply** (navigate home with the saved query), inline **rename** (`rename-view-<id>`), **pin/unpin** toggle (`toggle-pin-<id>`, `PATCH {pinned}` — pinned views become sidebar dashboards), two-step **delete** (`delete-view-<id>`), and **up/down reorder** (`view-up-<id>`/`view-down-<id>`, sends the full reordered id list to `POST /api/saved-views/reorder`). Empty state (`saved-views-empty`). Reached from the homepage **Saved views** button (`[data-testid="manage-saved-views-link"]` → `/saved-views`, in the dashboard controls row beside the Save-view / Fields menus) — there is no longer a sidebar link. Views are created from the homepage **Save view** popover (`SaveViewMenu.vue`, `[data-testid="save-view-menu"]` beside the card-fields menu) which serialises the current `buildDocumentQuery(applied)` state; pinned views render as **first-class sidebar links** (one `sidebar-dashboard-<id>` RouterLink each → `/` with the saved query) directly under the **Documents** entry — no "Saved views" heading, no separate subsection. See [api.md §1.20](api.md). |
| `JobsView` | `/jobs` (`jobs`) | Background-jobs dashboard, split into **Active** (queued/running) and **Recent** (finished) sections, one row per document (the server collapses a document's jobs to its latest — [api.md §1.8](api.md)). **Document-less system rows** (email poll, series insight) render a grey **`System` chip** + humanised task name in the Document cell (`[data-testid="jobs-system-label"]`) instead of empty em-dashes. A **filter bar** (`[data-testid="jobs-filter-bar"]`) offers a **task-type** `AppSelect` (options from `GET /api/jobs/task-names`) and a **document typeahead** (`#jobs-document-filter`, searches `GET /api/documents?q=`); choosing a document switches the server to **history mode** (every job for it, newest first — the heading becomes **History**) and shows a removable chip (`[data-testid="jobs-document-chip"]`). Both filters live in the **URL query** (`?task=&document_id=`), so `/jobs?document_id=<id>` deep-links a document's history (the detail page's **View job history** link). A **Columns** menu toggles per-column visibility (persisted to `localStorage`). **Live updates:** the view watches `jobsStore.lastEvent` and refetches on *every* document event (catching intra-pipeline stage changes that leave `activeCount` unchanged); while **Show system tasks** is on it also polls every 10 s, since system tasks emit no SSE event. |
| `HeldEmailsView` | `/held-emails` (`held-emails`) | The **hold-for-review queue**: emails the ingest pipeline held instead of filing (semantics in [ingestion.md](ingestion.md), "Held for review"; endpoints in [api.md §1.21](api.md)). A status filter (`[data-testid="held-emails-status-filter"]`: Held *(default)* / Ingested / Dismissed / All) over rows (`held-email-row`) showing sender/subject/date, a **verdict chip** (`held-email-verdict`) + reason line, and a lazy-expanded **structured per-item decision trace** (loaded via GET detail; parallel markup to `DocumentHistoryTimeline`'s "Email triage" breakdown — one line per item: `filename ?? '<body>'` → stage → verdict (reason), plus From/Subject chips). Row actions: **Ingest anyway** (queues the override task; the row shows a queued state while the `heldEmails` store polls it to resolution) and **Dismiss** (DB-only, immediate). Resolved rows show the outcome, links to any created documents, and `last_error`. Fed by `src/api/heldEmails.ts` + `stores/heldEmails.ts`. Reached from the dashboard's held-emails affordance (below) or directly. |
| `AdminView` | `/admin` (`admin`, `meta.adminOnly`) | **Admin-only** (the `authGuard` redirects non-admins to `/`; the sidebar link is hidden unless `auth.isAdmin`). A `role="tablist"` page with five tabs (`[data-testid="admin-tab-<id>-btn"]` / panels `admin-tab-<id>`), each backed by an `/api/admin/*` endpoint ([api.md §1.18](api.md)): **System** (version + git sha, deployment topology, redacted config table, DB stats), **Architecture** (`architecture.md`/`ingestion.md` rendered through the shared `marked` + DOMPurify pipeline), **Coverage** (backend/frontend % vs gate, or an "unavailable" banner), **Users** (table with role/active badges + per-row promote/demote/activate and a create-user form; the current user's self-actions are hidden, and the last-admin 409 surfaces inline), and **Metadata** (reference-taxonomy management, grouped into **Senders**, **Recipients**, **Kinds** and **Currencies** cards, each lazily loaded on first opening the tab). Senders and recipients share the id-keyed **create / rename-with-merge-on-409 / delete-with-reassign** UI; kinds are slug-keyed with a **name-only rename** (a collision is a row error — no merge) and reassign-by-slug delete; the **Currencies** card lists codes-in-use with counts and a series-aware **normalise** form (from-select + to-input) behind a confirm step, surfacing the per-table result, an FX-missing warning, or a refusal listing override conflicts. After any mutation it reloads the list and refreshes the shared taxonomy cache so other views' dropdowns update; all names render via text interpolation (no `v-html`). See [admin.md §1.2.3](admin.md). |
| `LoginView` | `/login` (`login`, `meta.public`) | **Bypasses the shell** — a centered `w-full max-w-md` Mosaic card on a `bg-gray-100 dark:bg-gray-900` background; `AppInput` + `AppButton` + `AppErrorSummary`. |

### Search modal (`src/components/SearchModal.vue`)

A native `<dialog>` (`showModal()`) mounted once in `DefaultLayout`. Three
entry points, all funnelling into the same instance: the header search button,
the sidebar **Search** nav item (both emit `open-search` →
`searchModal.open()`), and pressing **`/`** anywhere outside a form field.
It exposes `open()` via
`defineExpose`, pre-fills its fields (`AppInput` query, `AppSelect`
kind/sender/tag/language fed lazily from the cached taxonomy endpoints,
`AppDateInput` from/to) from the current route query, and on submit pushes the
query to the documents route. Native dialog semantics give focus containment,
ESC-to-close and `::backdrop`; focus is handed back to the opener on close.
Layout lives in `.app-search-modal` (`utility-patterns.css`): a centered
`max-w-2xl` card on desktop, full-screen below 640px. It reasserts
`margin: auto` because Tailwind Preflight zeroes the margin that the browser
otherwise uses to centre a modal `<dialog>`.

The inline filter bar (below) is visible at all screen sizes. The pill row is
always shown and **wraps** onto multiple rows on narrow screens (there is no
collapse toggle), so status and multi-tag filtering stay available on mobile.
The modal remains available at any size (e.g. via the `/` shortcut) and writes
the same URL query.

### Dashboard filter bar (`src/components/DocumentFilterBar.vue`)

An always-visible search-and-filter bar rendered by `DocumentListView` in the
dashboard hero area (replacing the old plain-text "Filtered by …" summary line).
The URL remains the single source of truth; all reading/writing of query
parameters goes through `src/utils/documentQuery.ts` (`parseDocumentQuery` /
`buildDocumentQuery` / `hasActiveFilters`), so the modal and the bar stay
in sync automatically.

- **Search input:** debounced 300 ms — typing pushes `?q=` via
  `router.replace`; pressing Enter applies immediately via `router.push`.
- **Filter pills:** Kind, Sender, **Recipient** (single-select —
  `?recipient_id=<id>`, `[data-testid="pill-recipient"]`, options from the
  shared taxonomy cache via `GET /api/recipients`), Date range, Tag (multi-select —
  `?tag=a&tag=b`), **Project** (multi-select via `AppCheckboxes`, mirroring the
  Tag pill — `?project=a&project=b`, which **OR**-compose, unlike Tag's AND;
  options from the shared taxonomy cache), and a **More** pill covering Language +
  Status.
- **Business-matter quick filters:** a pill row directly below the main pills
  (`[data-testid="matter-filters"]`), one pill per **matter** that has documents
  (`[data-testid="matter-filter-<slug>"]`), ordered **most-numerous first** (by
  `document_count` from the shared taxonomy cache / `GET /api/matters`, ties
  broken by name); zero-count matters are omitted. The row is a **single line
  that scrolls sideways** rather than wrapping (`overflow-x-auto whitespace-nowrap`,
  pills `shrink-0`). Unlike the single-select Kind pill, matters **multi-select**
  (OR-compose): clicking toggles a matter in/out of `?matter=a&matter=b`, so a
  second pill keeps the first active; clicking an **active** pill (violet,
  `aria-pressed="true"`) removes just that one. Resets to page 1 like any other
  filter change. (A document-type quick-filter row was removed 2026-07-20 to
  declutter the bar — kind filtering remains via the **Kind** dropdown pill.)
- **Active-filter chips:** each applied filter renders as a removable chip
  below the pill row; a **Clear all** button removes every active filter at
  once.
- **Mobile:** the pill row is always visible and **wraps** onto multiple rows
  (no collapse toggle); the search input and chips stay visible at every width.
  On `DocumentListView`, the result count sits on its own row above the
  sort/tiles/save-view controls, which also wrap, so nothing is clipped on a
  narrow screen.
- **`FilterPill` primitive** (`src/components/app/FilterPill.vue`, exported
  from `@/components/app`): a rounded pill button + slotted dropdown panel,
  `v-model:open`. It builds on **`AppPopover`** (§1.4) for the shared overlay
  behaviour — closes on Escape (focus returns to the pill) or outside mousedown,
  viewport-aware alignment — and adds the pill's active/value-label styling.
- **`status` filter** and **multi-tag** (`tags: string[]`) are new additions
  to `AppliedFilters`; the `DOCUMENT_STATUSES` options array lives in
  `src/api/documents.ts`.

### Dashboard sort control (`DocumentListView.vue`)

A mosaic sort control sits in the results-count row: a field `<select>`
(`[data-testid="sort-field-select"]` — Date on document / Date added to library) plus a violet
asc/desc toggle (`[data-testid="sort-dir-toggle"]`). It round-trips through the
URL like the filters — `sort`/`dir` are added to `AppliedFilters`,
`parseDocumentQuery`/`buildDocumentQuery` (omitted at their defaults:
`added_date`/`desc`), and unknown values fall back to the defaults. The choice is
also **remembered** per machine in `localStorage['library:doc-sort-v1']`:
`setSort` writes the preference, and `parseDocumentQuery` takes it as the
fallback whenever the URL carries no `sort`/`dir`, so a bare `/` reproduces the
last selection. Because the frontend default (`added_date`) differs from the
API's own default (`document_date`), `buildFilters` **always sends `sort` +
`direction` explicitly** to the list endpoint. Sort is deliberately **excluded
from `hasActiveFilters`** (it is not a filter). While a search query is active
the control is disabled, because the backend orders by relevance rank when `q`
is present. The **Fields** button (§ card-fields picker) sits in this same
right-aligned controls row, alongside the tiles-per-row select and a **Saved
views** link (`[data-testid="manage-saved-views-link"]` → `/saved-views`).

### Dashboard "Needs review" affordance (`DocumentListView.vue`)

Above the results, a **"Needs review"** button (`[data-testid="needs-review-filter"]`)
toggles the `review=needs_review` URL filter. It reads as a **collapsed section**,
not a pill: a full-width-on-mobile `rounded-md` block with a warning icon and the
**count** in its label ("*N* document(s) need review"). The count is a cheap
total-only probe (`listDocuments({ review_status: 'needs_review', limit: 1 })`,
refreshed on each list load) independent of the current filter. When the count is
zero and the filter is off the button is **hidden**; when documents need review it
carries a **pale-red bg + darker-red border** (active state deepens the red).
Beside it, a violet **"Review these one by one →"** button
(`[data-testid="start-review-queue"]`, shown when the count > 0) enters the
step-through review queue (below).

On each flagged tile, a short **plain-language reason** (`[data-testid="review-reason"]`)
sits next to the "Needs review" badge — e.g. *"Unlikely date"* — sourced from the
new `review_findings` on the list row and humanised by `summarizeReviewReasons`
(`utils/validationReason.ts`, the single source of finding wording shared with
the detail why-panel and the queue).

### Dashboard held-emails affordance (`DocumentListView.vue`)

Beside the "Needs review" button in the same attention row, a violet **"N
emails held →"** link (`[data-testid="held-emails-button"]`, → `/held-emails`)
appears whenever the `heldEmails` store's count probe is non-zero (refreshed on
each list load, like the needs-review count). Hidden at zero — the hold queue
is invisible until there is something to review. Held emails are deliberately
**not** part of the needs-review count: *held* means "no document was filed
yet", not "a filed document needs checking" (see [ingestion.md](ingestion.md),
"Held for review").

### Step-through review queue (`stores/reviewQueue.ts` + queue mode on `DocumentDetailView`)

The **"Review these one by one"** button loads every `needs_review` id into the
`reviewQueue` Pinia store (ordered ids + a cursor) and opens the first document
with `?queue=1`. In **queue mode** `DocumentDetailView` shows a violet queue bar
(`[data-testid="review-queue-bar"]`) with the position ("Reviewing *X* of *N*")
and controls: **← Prev**, **Verify & next** (accepts as-is via the verify
endpoint), **Next →**, and **Exit**. Editing is the page's normal per-field
autosave — which now revalidates server-side (api.md §1.5) — so fixing a field
drops the document off `needs_review`; **Next** then removes the resolved
document from the queue and advances, while an unfixed document is kept for a
later pass. When the queue empties the view returns to the dashboard. No new
route or editor: queue mode is a query flag reusing the whole existing detail
page. Covered by `stores/__tests__/reviewQueue.spec.ts`, queue-mode cases in
`DocumentDetailView.spec.ts`, and `e2e/review-queue.spec.ts`.

### DocumentDetailView component structure

The view keeps the hero, the two-column grid, the preview column + markdown
reader, the actions card, and the history timeline; the two editors are their
own components: **`DocumentMetadataEditor.vue`** (the metadata editor — now
rendered **once per section** via a `section` prop, so the former single
"Details" card is **four independent tiles**: Content (which also holds Kind +
Language), Sender, recipient & dates, Financial, and a read-only System tile)
and **`NoteEditorPanel.vue`** (the in-place note editor + version history).
Because every save **replaces** the parent-owned `doc` wholesale (and the hero /
preview read `doc`), both editors are wired **`v-model:doc`** — the child emits
the fresh document up so the parent's other regions re-render (a one-way prop
would freeze them on the pre-save snapshot). `NoteEditorPanel` additionally emits
**`reload-markdown`** because the note body lives in the parent's reader
(`markdownData`), not on `doc`. Shared `marked`+DOMPurify/format helpers live in
`src/utils/documentFormat.ts`. `hydrateDrafts` runs on the shared edit-mode flag
flipping on (a `watch(editMode)`, so any of the hero toggle / Action dock reaches
every mounted tile) plus once on mount if edit mode is *already* on (a value-less
tile — e.g. Financial — is hidden in read mode and first mounts only after
editing begins, so its watch never fires) — never on a `watch(doc)`, so a
background refresh mid-edit can't clobber in-progress drafts.

The detail page also leads with a prominent **"Why this needs review"** panel
(`[data-testid="validation-findings"]`, shown while `review_status` is
`needs_review`) that lists **every** finding in plain language — including
field-mapped ones like an implausible date, which previously showed only as a
small per-field ⚠ badge. Each finding leads with a friendly **field-label chip**
(`[data-testid="reason-field"]`, e.g. "Date on document", "Amount" — mapped from
the finding's storage field in `utils/validationReason.ts`) naming the flagged
attribute, so it is clear *what* to check. The per-field ⚠ badges remain as a
secondary signal. The **"Mark verified"** button (`[data-testid="mark-verified"]`,
and the review queue's **"Verify & next"**) is shown **only while `needs_review`**
— an `unreviewed` document has nothing flagged, so there is nothing to verify and
no button; verifying is thus strictly the resolution of a flagged document.

**Edit layout mode** is a single page-wide toggle in the hero
(`[data-testid="edit-layout-toggle"]`, label **Edit layout** / **Done**,
`aria-pressed`) that drives the **`useDocumentLayout`** composable
(`src/composables/useDocumentLayout.ts`) — a singleton backed by `localStorage`
(per-machine, all documents; the mode flag itself is ephemeral and resets on
reload). This is **distinct** from the hero's **Edit details** `edit-toggle`
(which edits metadata *values* across the section tiles): Edit layout only
rearranges *presentation*. Turning it
on reveals, in the hero, one reorderable row per known field
(`[data-testid="hero-field-{key}"]`) with a **show/hide** checkbox
(`hero-field-toggle-{key}`) and a drag handle, plus a **Reset layout** button
(`[data-testid="reset-layout"]`); and, on each section card, a drag handle
(`card-drag-handle-{id}`) over its wrapper (`section-card-{id}`). Drag is
`sortablejs` (instances built on the container refs when the mode turns on and
destroyed when it turns off / on unmount); each `onEnd` translates the DOM move
into a composable setter (`moveHeroField` / `moveCard`) — the reactive
state is the source of truth and Vue re-renders from it. Hero fields render in
the saved order (read mode: visible-and-populated only; edit mode: **all** known
fields, empty ones with an em-dash placeholder so they stay toggleable).

Section cards use a **free-form, cross-column** layout: `useDocumentLayout`
persists `cardColumns: { left: string[], right: string[] }`
(`library:doc-layout-card-columns-v1`) rather than one flat order, and the
metadata (left) and preview (right) columns' two SortableJS instances share
one `group` (`'doc-cards'`), so a card can be dragged from either column into
the other, not just reordered within its own. Both columns render their cards
from **one shared card template** — defined once via VueUse's
`createReusableTemplate` (`<DefineCard v-slot="{ cardId }">` holds the drag
handle plus every card body; each column's `v-for` reuses it with
`<ReuseCard :card-id>`), so a card draws its body in **whichever column
currently holds it**. (Before this, the two columns had *disjoint*
`v-if cardId===…` chains, so dragging a card into the other column dropped it —
the destination had no branch for its id and the wrapper collapsed via
`empty:hidden`.) Each column renders its full, persisted id list filtered to
cards actually present for this document via `cardPresent(id)`: `notes` only for
note docs; `series-chart` gated on `DocumentSeriesTrend`'s emitted `presence`;
and `preview` only when it would render real content — an image/PDF viewer, a
downloadable binary original, or (once the text has loaded and is empty) the
"no preview" fallback — so a text-only note no longer renders an empty preview
`.card` (a stray thin line) or, in edit mode, a drag handle attached to no
panel. The metadata section tiles add their own `cardPresent` rule: **Content
and System always show; Sender, recipient & dates / Financial appear only
when they hold a value OR metadata edit mode is on** — so an empty tile (e.g.
Financial on a non-financial document) stays hidden in read mode but reappears to
be filled in while editing. The default split is left: `notes` ·
`metadata-content` · `metadata-parties` ·
`metadata-financial` · `metadata-system` · `comments` · `actions` · `history`,
right: `preview` · `markdown` · `series-chart`. On drop,
`onCardDragEnd` reverts SortableJS's own
DOM move (so Vue's re-render from `cardColumns` is the only thing that ever
places the node — otherwise the card would briefly exist twice when it
crosses into the other column's DOM subtree) and translates the rendered
drop index back into a full-list index before calling `moveCard(cardId,
toColumn, toIndex)`. **Reset layout** restores both `heroFields` and
`cardColumns` to their defaults. A user's pre-existing flat
`library:doc-layout-card-order-v1` order (from before this two-column model)
is migrated once, on first load, by splitting it into `left`/`right` along
the same preview/metadata boundary, so nothing visibly jumps for an existing
user; the legacy key is then left untouched (no longer read or written).
Separately, a saved layout that still holds the pre-split single `metadata`
card is migrated once on load (`migrateMetadataCard`) by expanding it **in
place** into the five `metadata-*` tiles, so a user who moved the Details card
keeps its position rather than having the new tiles appended at the column's end.
Covered by `DocumentDetailView.spec.ts` and `useDocumentLayout.spec.ts`.

### Dashboard card-fields picker (`DashboardFieldsMenu.vue` / `DashboardFieldsEditor.vue`)

A **Fields** button (`[data-testid="dashboard-fields-button"]`) on the dashboard
opens a popover to toggle and reorder the metadata fields shown on document
cards. The stored `dashboard_fields` list is **order-significant**:
`DocumentListView` renders its card meta row by iterating that ordered list (the
ungated "Needs review" badge stays pinned first, outside the field set). The
reusable `DashboardFieldsEditor` provides a checkbox per field, drag reorder
(SortableJS via the `sortablejs` dependency), accessible Up/Down move buttons +
aria, and "Reset to defaults". Changes persist immediately through the existing
`PUT /api/settings` → `auth.applyPreferences` path — no new endpoint. The
Settings → Dashboard tab reuses the same `DashboardFieldsEditor`.

The field catalog (`DASHBOARD_FIELDS` in `api/settings.ts`) mirrors the detail
hero's **five document dates** so both surfaces distinguish the same set:
**Date on document** (`date`, the document's own date — value kept for back-compat),
**Due date** (`due_date`), **Expiry date** (`expiry_date`), **Date added to library**
(`added_date` → `created_at`) and **Last edited** (`last_edited` → `updated_at`).
On a tile every date renders with a short muted prefix (Date / Due / Expires /
Added / Edited, no colon) so several dates stay unambiguous and tile metadata
reads consistently as key: value — Amount stays bare (currency self-identifies)
and Sender stays a plain name. `created_at`/`updated_at` show their date portion
only. Only `date` is enabled by default. The list API returns all five dates on
every item (see api.md §1.10.2).

### Admin → Metadata tab (`AdminView.vue`)

The Metadata tab manages the reference taxonomy with full CRUD, grouped into
**Senders**, **Recipients**, **Kinds**, and **Currencies** cards (lazy-loaded on
first open). Senders and recipients share the id-keyed create / rename-or-merge /
delete-with-reassign UI (`sender-*`, `recipient-*` testids); kinds are slug-keyed
with a name-only rename (a name collision is a row error — no merge) and
reassign-by-slug delete (`kind-*` testids). The **Currencies** card lists codes
in use with counts and offers a series-aware **normalise** form (from-select +
to-input) behind a confirm step, surfacing the per-table result, an FX-missing
warning (`currency-fx-warning`), or a refusal listing override conflicts
(`currency-conflict`). It also carries an **FX rates** subsection
(`GET /api/admin/fx-rates`) listing each in-use code's rate status
(`fx-row-{code}`, `fx-status-{code}`): USD as base, a seeded rate + as-of, or
**No rate** with a **Fetch rate** button (`fx-fetch-{code}`, live provider) and
an **Enter manually** fallback form (`fx-manual-toggle-{code}` →
`fx-manual-input-{code}` + `fx-seed-submit-{code}`), which also opens
automatically when a live fetch fails. All mutations go through
`src/api/admin.ts` and refresh the shared taxonomy cache.

**Component structure.** `AdminView.vue` is a thin shell (PageHeader + tablist +
one `v-show` section per tab); each tab is its own component under
`src/views/admin/` (`AdminSystemPanel`, `AdminArchitecturePanel`,
`AdminCoveragePanel`, `AdminUsersPanel`, `AdminMetadataPanel`). The eager tabs
self-load on mount; `AdminMetadataPanel` takes an `:active` prop and loads lazily
on first open. The Senders/Recipients/Kinds cards are three instances of one
generic **`TaxonomyCrudPanel.vue`** driven by a `TaxonomyDescriptor`
(`src/views/admin/taxonomyCrud.ts`): the descriptor captures every point the
three entities diverge — `keyOf` (id vs slug), `hasMerge` (kinds have none),
`parseReassign`, and the API callables — so the shared panel stays
behaviour-identical to the original three inline blocks. Currencies + FX stay
inline in `AdminMetadataPanel` (a different per-row-state idiom, not taxonomy).

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
- `npm run test:e2e` — Playwright against the real stack. Five projects: desktop Chromium, mobile WebKit (375 px, iPhone 14), tablet WebKit (iPad gen 11), **desktop Firefox**, and **desktop WebKit** (Safari). The chromium/mobile/tablet projects run the full suite; the two desktop-engine projects are **scoped (via `testMatch`) to `e2e/pdf-preview.spec.ts` only** — they exist to prove the self-rendered PDF preview behaves identically across all three engines, without forcing the rest of the suite onto Firefox. That spec proves canvases paint and scrolling reveals page 2 on each engine. Recent flows have their own specs in `frontend/e2e/`: **`markdown-reader`** (upload a `.md` → the reader renders), **`projects`** (create → assign via the token multiselect → see it on the `/projects` index → filter the dashboard by it), **`notes`** (author a note → edit in place → restore a version), **`topics-readonly`** (topics show as read-only badges with no editor), **`admin-views`** (a normal user sees no Admin link and is redirected from `/admin`; an admin reaches `/admin` and the four tabs render), **`tile-border-colour`** (sets a per-kind override and asserts the tile's *computed* border colour — the cascade-layer regression guard), **`review-queue`** (a future-date edit flags a doc `needs_review`, then the queue is entered, advanced, and exited), and **`held-emails`** (navigation + empty state only — the e2e stack has no IMAP, so the hold flows live in vitest + backend tests). The admin spec needs a second admin login (`E2E_ADMIN_USERNAME`/`E2E_ADMIN_PASSWORD`; CI creates an `e2e-admin --admin` user). All self-skip without `E2E_BASE_URL` and run in CI's e2e job. (CI installs all three engines — `chromium firefox webkit` — in `.github/workflows/ci.yml`.)

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
