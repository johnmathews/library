# Frontend

**Status:** active. **Last updated:** 2026-09-01 (the chart rule editor, issue #135: `ChartRuleEditor.vue`, `spending/ruleText.ts` and `composables/facetVocabulary.ts` join the `components/spending/` set, the `SpendingWorkspaceView` row gains the **Edit rule** trigger and the two things `onRuleSaved` must and must not do, and `SpendingCard`'s overflow menu is restated as a closed list of four with a note on where rule editing lives instead. `Covers:` gains `components/spending/`, `spending/` and `composables/` — the first draft named only `views/`, `router/index.ts` and `components/layout/`, which did not reach a single one of the three modules this change added to the enumerations, so the gate could not have flagged this document for the edit it was making.) Earlier: 2026-08-31 (the legacy series stack was deleted, and this document loses everything that described it: the `ChartsView` (`/charts/legacy`) and `SeriesChartView` (`/charts/:seriesId`) route rows, the `DocumentSeriesTrend`/`SeriesChartTile` block in the `DocumentDetailView` row, the `series-chart` card in the document-detail layout (with a note on how a stored layout naming it is reconciled), `llm-surface-series_insight` in the `SettingsView` row, `series insight` in `JobsView`'s system-row example, and the currency form's "series-aware"/conflict-list wording in the `AdminView` row. §1.7.0 is rewritten for the renamed `retrieval-recall` job — the workflow survives, the browser journey and its did-it-actually-run assertion do not.) Earlier: 2026-08-30 (`/charts` becomes the spending board: `SpendingBoardView.vue` and `SpendingWorkspaceView.vue` join the routes table, alongside the `components/spending/` set — `SpendingChart`, `SpendingLegend`, `SpendingFooter`, `SpendingCard`, `SpendingDrillPanel` and its three bodies, `QuestionDraft`, `SpendingEmptyState` — and the two pure modules `spending/money.ts` and `spending/palette.ts`, in a new "Spending board and workspace" subsection. `ChartsView.vue` moves to `/charts/legacy`, unlinked from the sidebar — it holds the only Smart Groups creation UI and survives until a later plan deletes it with the series backend it serves. A second container-query threshold (`@3xl`, 768px) is recorded beside the existing `@5xl` header one, with the measured border-box/content-box table and the top-layer constraint that keeps the drill panel off container queries entirely. Earlier (2026-08-28): documented the facet vocabulary UI, which had no mention here: a new "Facet filter bar" subsection for `FacetFilterBar.vue` (AND-composed selects, facets with no values omitted, URL/saved-view persistence, its own "Clear facets" button, no active-filter chip) and a new "Facet editor" subsection for `FacetEditor.vue` (every facet including empty ones rendered disabled, only changed facets sent, a cleared facet sent as explicit `null`, and its exclusion from the drag-reorder card-columns system flagged as a known limitation rather than intentional design). See `docs/facets.md` for the vocabulary itself. Earlier (2026-08-25): Settings gains an **Ask** tab — the free-text "About you" notes Ask reads with every question; §1.3 `SettingsView`, `auth.askProfile`). Earlier (2026-08-22): `/matters` and `/projects` gain a one-sentence `PageHeader` lede explaining what each feature is for. Earlier the same day: `PageHeader` gains a `#controls` slot: `/charts`, `/jobs` and `/matters` render one header toolbar instead of an actions row above a filter row, merged on a **container** query so the same viewport merges or stacks depending on the sidebar; `/jobs`' filter bar rebuilt to the §5 label recipe). Earlier the same day: §1.5 `AskView`: correct two clauses left stale by the app-bar title move. Earlier the same day: the page title moves into `AppHeader`; `PageHeader` keeps only the lede + actions and renders nothing for a bare title. Also: the Ask composer is one flat full-width bar — the nested pill is gone). Earlier (2026-08-20): Settings gains an **LLM backend** tab — the instance-wide metered-API vs Claude-subscription choice per surface, admin-editable, read-only for everyone else; §1.3 `SettingsView`; badges colour-coded via AppBadge's `colour` prop, and the override badge reworded from "Overridden (deployed default: …)" to "Changed here" with the reset button naming its target value). Earlier: 2026-08-13 (the `index.html` sidebar seed now mirrors the store's full key precedence, so §1.2's note about it reading only the legacy key no longer applies; earlier, 2026-08-12: nightly Smart Groups heartbeat §1.7.0 and the Playwright-not-jsdom layout rule §1.7.3; earlier the same day, documentation verification sweep: documented `MattersListView`, the Matters sidebar link and filter pill, the Notifications settings tab, `DefaultLayout`'s toast container and SSE ownership, and PWA wiring (new §1.6.1); corrected the doc-grid column defaults, the Jobs view's table shape and `AppPopover`'s backers; scoped §1.8 as historical. Earlier the same day: facet vocabulary panel, Task 11: new `VocabularyView` row in §1.5's views table — the tab shell, each panel's lazy-on-first-activation load and its `{ immediate: true }` deviation from the `AdminMetadataPanel` pattern, `ValueMergeView`'s target-specific dry-run gating and stale-response guard, and the `@container`/`@md:` row in `FacetsPanel.vue` with a pointer to [frontend-view-principles.md §5.1](frontend-view-principles.md); §1.3's `AppSidebar` nav-order list gains the **Vocabulary** entry between Matters and Settings). Earlier (2026-08-28) (documented the facet vocabulary UI, which had no mention here: a new "Facet filter bar" subsection for `FacetFilterBar.vue` (AND-composed selects, facets with no values omitted, URL/saved-view persistence, its own "Clear facets" button, no active-filter chip) and a new "Facet editor" subsection for `FacetEditor.vue` (every facet including empty ones rendered disabled, only changed facets sent, a cleared facet sent as explicit `null`, and its exclusion from the drag-reorder card-columns system flagged as a known limitation rather than intentional design). See `docs/facets.md` for the vocabulary itself. Earlier (2026-08-25): Settings gains an **Ask** tab — the free-text "About you" notes Ask reads with every question; §1.3 `SettingsView`, `auth.askProfile`). Earlier (2026-08-22): `/matters` and `/projects` gain a one-sentence `PageHeader` lede explaining what each feature is for. Earlier the same day: `PageHeader` gains a `#controls` slot: `/charts`, `/jobs` and `/matters` render one header toolbar instead of an actions row above a filter row, merged on a **container** query so the same viewport merges or stacks depending on the sidebar; `/jobs`' filter bar rebuilt to the §5 label recipe). Earlier the same day: §1.5 `AskView`: correct two clauses left stale by the app-bar title move. Earlier the same day: the page title moves into `AppHeader`; `PageHeader` keeps only the lede + actions and renders nothing for a bare title. Also: the Ask composer is one flat full-width bar — the nested pill is gone). Earlier (2026-08-20): Settings gains an **LLM backend** tab — the instance-wide metered-API vs Claude-subscription choice per surface, admin-editable, read-only for everyone else; §1.3 `SettingsView`; badges colour-coded via AppBadge's `colour` prop, and the override badge reworded from "Overridden (deployed default: …)" to "Changed here" with the reset button naming its target value). Earlier: 2026-08-13 (the `index.html` sidebar seed now mirrors the store's full key precedence, so §1.2's note about it reading only the legacy key no longer applies; earlier, 2026-08-12: nightly Smart Groups heartbeat §1.7.0 and the Playwright-not-jsdom layout rule §1.7.3; earlier the same day, documentation verification sweep: documented `MattersListView`, the Matters sidebar link and filter pill, the Notifications settings tab, `DefaultLayout`'s toast container and SSE ownership, and PWA wiring (new §1.6.1); corrected the doc-grid column defaults, the Jobs view's table shape and `AppPopover`'s backers; scoped §1.8 as historical)
**Last verified:** 2026-09-01 — method: partial, scoped to the three enumerations this change touches and nothing else. Read `ChartRuleEditor.vue` and `spending/ruleText.ts` in full; re-enumerated `SpendingWorkspaceView.vue`'s controls from its `data-testid`s and confirmed `workspace-edit-rule` sits in `PageHeader`'s `#actions`, not inside `workspace-toolbar` (which carries `toolbarRowClass`'s `hidden` below `@3xl/workspace`) — that placement is what the "reachable at every width" claim rests on. Re-enumerated `SpendingCard.vue`'s menu testids as exactly four. The `onRuleSaved` claims are not merely read: adding `initControlsFromChart` was observed turning "keeps the range the owner was looking at" red, and removing the explicit `loadData()` was observed turning both refetch tests red (`expected 1 to be 2`). The `AppMultiSelect` rejection was checked against that component — `createCandidate` is computed from the prop list alone and rendered unconditionally, so there is no prop that suppresses it. Geometry claims are **not** re-measured this pass: the 343px column figure is carried from `e2e/spending-layout.spec.ts`'s recorded table, and the row's stacking is asserted there rather than here. Frontend suite 1409 tests green (run with `--maxWorkers=2`; the default pool was starving on this machine and timing out unrelated specs), eslint and `vue-tsc` clean. Everything outside those three enumerations carries forward unchanged. Earlier: 2026-08-31 — method: partial, scoped to every claim about the deleted series surfaces. Confirmed by `ls frontend/src/views/` and `git log` that `ChartsView.vue`, `SeriesChartView.vue`, `SeriesChartTile.vue`, `ChartControls.vue` and `DocumentSeriesTrend.vue` are gone from the tree; read `DEFAULT_CARD_COLUMNS` in `frontend/src/composables/useDocumentLayout.ts` (`right: ['preview', 'markdown']` — no `series-chart`) and the reconciliation test that pins a stored `series-chart` being dropped without losing its neighbours' order; read the `#controls`/`#actions` slots of `views/JobsView.vue` and `views/SpendingWorkspaceView.vue` in full for the exemplar claims now in `frontend-view-principles.md`; read `.github/workflows/e2e-nightly.yml` end to end for the rewritten §1.7.0 (job `retrieval-recall`, `continue-on-error: true`, the in-network `http://embedder:80/health` poll, no `E2E_SMART_GROUPS` and no `assert-e2e-ran.mjs` step); and grepped `llm-surface` in `views/SettingsView.vue`, which renders `llm-surface-${surface.surface}` off the API's list rather than a hard-coded pair. Everything not about the series stack carries forward unchanged. Earlier the same day — method: re-stamp only. The #130 squash-merge landed on 2026-08-31, dating this file's last edit a day after its stamp, which `check_docs` reports as `stale-doc-edit` (the known date jump, issue #126 — the same re-stamp #122 made after #121). No prose changed; the verification below stands as performed. Previously verified 2026-08-30 — method: the spending board/workspace geometry (the `@3xl`/`@5xl` table and the top-layer drill-panel claim) was measured in the real stack via Playwright, the same method as the existing `@5xl` row, and both container-query guards were demonstrated failing under a swapped `lg:` viewport query before being trusted (`e2e/spending-layout.spec.ts`, `e2e/header-toolbar.spec.ts`); the shared six-slot split palette (`@/utils/splitPalette`) was verified against this app's own chart surfaces with an independent validator (all-pairs contrast check across both themes), not just read from its own tests. The rest of the document carries forward its 2026-08-28 verification: method: read `src/components/facets/FacetFilterBar.vue` and `src/components/facets/FacetEditor.vue` in full; read their wiring in `src/components/DocumentFilterBar.vue` (the `facets`/`onFacetChange` block and the template around `[data-testid="facet-filter-bar"]`, confirmed facet selections carry no chip in the `chips` computed) and `src/views/DocumentDetailView.vue` (the `FacetEditor` mount and its surrounding SortableJS-coupling comment in the metadata column template); read `src/utils/documentQuery.ts`'s `facet` parse/serialise pair for the URL persistence claim. Not re-verified against `docs/roadmap.md` — the drag-reorder exclusion is not currently tracked there; this document states it as a known limitation without claiming a tracking location. No frontend tests were executed for this pass (docs-only change; `FacetFilterBar.spec.ts` and `FacetEditor.spec.ts` were read, not re-run). The rest carries forward its previous verification: 2026-08-25 — method: partial re-verification, scoped to the new Ask-tab sentence in the `SettingsView` row, read against `SettingsView.vue`, `stores/auth.ts` and `api/settings.ts` and covered by executed unit tests (`SettingsView.spec.ts` Ask-tab cases, `auth.spec.ts`, `api/__tests__/settings.spec.ts`) in a full frontend run; `vue-tsc` and eslint clean. Not screenshotted — it is a form, not a geometry claim. The rest carries forward its previous verification: 2026-08-22 — method: partial re-verification, scoped to the new `#controls` paragraph plus a wrap-up audit of the `JobsView` and `MattersListView` rows and the layout-spec list. Those three are **code-derived** claims and were checked by reading the shipped `JobsView.vue` / `MattersListView.vue` and `ls`-ing `frontend/e2e/` — which found and fixed a stale `AppSelect` claim on the jobs task filter, a missing `header-toolbar.spec.ts` in the spec enumeration, and an unmentioned matters archived toggle. The `#controls` paragraph is a **geometry** claim, so measured in the real stack (docker compose + the Vite dev server + Playwright) rather than read off class lists: the 1280px merge/stack split across sidebar states was measured directly (`#app-page` 1024px expanded → stacked, 1200px collapsed → merged), and the container-vs-viewport claim was confirmed by swapping `@5xl:` for `lg:` and watching `e2e/header-toolbar.spec.ts` go red. Backed by 1102 frontend unit tests and the full e2e suite (127 executed, all passing). The `AskView` §1.5 clauses carry forward their own 2026-08-22 verification: scoped to the `AskView` row's two mobile clauses, re-read against `AskView.vue` and `ConversationSidebar.vue` (the `max-lg:hidden` is on the view's `PageHeader` and on the rail's "New conversation" button; the mobile list bar's own `<h1>` is gone). Cross-checked against the 375px Ask-list screenshot taken from the running stack earlier the same day, which shows the app bar carrying "Ask" and the list screen carrying only the ＋. Earlier on 2026-08-22 the `AppHeader` section and the §1.5 composer sentence were verified against real-stack screenshots at 1440px light/dark and 375px, backed by 1099 frontend unit tests and the full e2e suite (123 executed, all passing). The rest of the document carries forward its 2026-08-20 verification: the `SettingsView` LLM-backend prose checked against `SettingsView.vue` and `frontend/src/api/settings.ts`, covered by `src/views/__tests__/SettingsLlmBackend.spec.ts` (10 tests, two confirmed to fail against the `variant` version) Plan 4c's vocabulary panel, verified the same day: 2026-08-30 — method: (facet vocabulary panel, Task 11) read `VocabularyView.vue`, `AppSidebar.vue`'s Matters/Vocabulary/Settings `RouterLink` block, `views/vocabulary/{FacetsPanel,SendersPanel,ValueMergeView}.vue`, and `router/index.ts`'s two `vocabulary*` entries in full, and diffed the new §1.5 row and §1.3 nav-order sentence against them. The lazy-load claim is `SendersPanel.vue`'s (and the identically-shaped `FacetsPanel.vue`/`SuggestionsPanel.vue`) `watch(() => props.active, ..., { immediate: true })`, whose own doc comment states the `{ immediate: true }` deviation from the literal `AdminMetadataPanel` pattern and why (the Facets tab is already active at mount). The merge page's target-specific gating is `ValueMergeView.vue`'s `canApply` computed and its `watch(target, ...)`'s eager `moved.value = null` reset plus the `if (target.value !== next) return` stale-response guard, cross-checked against `.superpowers/sdd/2026-08-30-facet-vocabulary-panel/task-7-report.md`'s mutation-check log (the stale-response guard is covered by an executed, git-committed test — `frontend/src/views/vocabulary/__tests__/ValueMergeView.spec.ts`'s "never lets a stale response for a superseded target attach to the current preview", observed failing under the guard's removal and passing restored, per that report). The `@container`/`@md:` claim is `FacetsPanel.vue`'s row wrapper class, and its correctness at the three e2e widths (not merely its presence) is the controller-run browser measurement recorded in `.superpowers/sdd/2026-08-30-facet-vocabulary-panel/progress.md` ("Controller-run browser verification (Task 6, Step 6)"): card widths 960/608/343 at viewports 1280/656/375, `@md:` giving a single-line row at all three while a substituted viewport `md:` wrongly stacks the row at 656 (card 608px is past the `@md` container breakpoint of 448px, but 656px is below the viewport `md` breakpoint of 768px) — read from that ledger, not re-measured in this pass. Covered by executed assertions run this pass: `cd frontend && npx vitest run FacetsPanel SendersPanel SuggestionsPanel VocabularyView ValueMergeView splitPalette slugify SplitColourPicker` — 8 files, 66 tests passed; `npm run lint` and `npm run type-check` not re-run this pass (docs-only change to files those checks don't cover — the frontend gate commands below cover the branch as a whole). Nothing else in this document was re-checked this pass; the rest carries forward its previous verification below unchanged. Earlier (2026-08-28) — method: read `src/components/facets/FacetFilterBar.vue` and `src/components/facets/FacetEditor.vue` in full; read their wiring in `src/components/DocumentFilterBar.vue` (the `facets`/`onFacetChange` block and the template around `[data-testid="facet-filter-bar"]`, confirmed facet selections carry no chip in the `chips` computed) and `src/views/DocumentDetailView.vue` (the `FacetEditor` mount and its surrounding SortableJS-coupling comment in the metadata column template); read `src/utils/documentQuery.ts`'s `facet` parse/serialise pair for the URL persistence claim. Not re-verified against `docs/roadmap.md` — the drag-reorder exclusion is not currently tracked there; this document states it as a known limitation without claiming a tracking location. No frontend tests were executed for this pass (docs-only change; `FacetFilterBar.spec.ts` and `FacetEditor.spec.ts` were read, not re-run). The rest carries forward its previous verification: 2026-08-25 — method: partial re-verification, scoped to the new Ask-tab sentence in the `SettingsView` row, read against `SettingsView.vue`, `stores/auth.ts` and `api/settings.ts` and covered by executed unit tests (`SettingsView.spec.ts` Ask-tab cases, `auth.spec.ts`, `api/__tests__/settings.spec.ts`) in a full frontend run; `vue-tsc` and eslint clean. Not screenshotted — it is a form, not a geometry claim. The rest carries forward its previous verification: 2026-08-22 — method: partial re-verification, scoped to the new `#controls` paragraph plus a wrap-up audit of the `JobsView` and `MattersListView` rows and the layout-spec list. Those three are **code-derived** claims and were checked by reading the shipped `JobsView.vue` / `MattersListView.vue` and `ls`-ing `frontend/e2e/` — which found and fixed a stale `AppSelect` claim on the jobs task filter, a missing `header-toolbar.spec.ts` in the spec enumeration, and an unmentioned matters archived toggle. The `#controls` paragraph is a **geometry** claim, so measured in the real stack (docker compose + the Vite dev server + Playwright) rather than read off class lists: the 1280px merge/stack split across sidebar states was measured directly (`#app-page` 1024px expanded → stacked, 1200px collapsed → merged), and the container-vs-viewport claim was confirmed by swapping `@5xl:` for `lg:` and watching `e2e/header-toolbar.spec.ts` go red. Backed by 1102 frontend unit tests and the full e2e suite (127 executed, all passing). The `AskView` §1.5 clauses carry forward their own 2026-08-22 verification: scoped to the `AskView` row's two mobile clauses, re-read against `AskView.vue` and `ConversationSidebar.vue` (the `max-lg:hidden` is on the view's `PageHeader` and on the rail's "New conversation" button; the mobile list bar's own `<h1>` is gone). Cross-checked against the 375px Ask-list screenshot taken from the running stack earlier the same day, which shows the app bar carrying "Ask" and the list screen carrying only the ＋. Earlier on 2026-08-22 the `AppHeader` section and the §1.5 composer sentence were verified against real-stack screenshots at 1440px light/dark and 375px, backed by 1099 frontend unit tests and the full e2e suite (123 executed, all passing). The rest of the document carries forward its 2026-08-20 verification: the `SettingsView` LLM-backend prose checked against `SettingsView.vue` and `frontend/src/api/settings.ts`, covered by `src/views/__tests__/SettingsLlmBackend.spec.ts` (10 tests, two confirmed to fail against the `variant` version).
**Covers:** frontend/src/views/, frontend/src/router/index.ts, frontend/src/components/layout/, frontend/src/components/spending/, frontend/src/spending/, frontend/src/composables/

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
- **Dashboard tiles:** `.app-doc-grid` (the responsive 2/2/3/4-column document
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
  densified so tiles fill as much of the screen as possible — reduced tile
  padding (`px-2 py-3` vs `sm:p-5`), a minimal inter-tile gap (`#dashboard-grid`
  gap `0.375rem` vs `1.5rem`), a near-edge gutter (the grid breaks out of the
  page's `px-4` padding with `margin-inline: -0.75rem`, leaving ~`0.25rem` to the
  screen edge — the header/filter bar keep the normal gutter), snug leading, and
  **abbreviated month names** in tile dates ("17 Sep 2019" rather than
  "17 September 2019", via a reactive `useMediaQuery('(max-width: 640px)')`).
  A **server-synced** account preference (`hide_summary_mobile` in
  `user.preferences`, on `auth.hideSummaryMobile`; **Settings → Appearance →
  Tile description on mobile**) drops each tile's description on phones to
  shorten the long/skinny tiles. It is applied with a reactive `v-if`
  (`hideSummaryOnMobile = hideSummaryMobile && isCompactDate`) that removes the
  `.app-doc-card__summary` node, **not** a CSS rule: the summary carries
  `line-clamp-3`, whose `display` lives in Tailwind's `utilities` layer and would
  out-rank any hide rule in the `components`-layer `utility-patterns.css`
  regardless of specificity. The search snippet is left in place (query context,
  not the always-on description); all of this reverts to the roomier desktop
  styling at `>= 640px`)
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

It also mounts **`ToastContainer`** (fed by the `notifications` store: error
toasts persist, everything else auto-dismisses after 5 s) and owns the lifetime
of the **live jobs SSE stream**, connecting the `jobs` store on mount and
disconnecting on unmount. Both live here rather than in a view because they are
app-wide and must survive route changes.

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
  `data-testid="sidebar-projects-link"`), **Matters** (`/matters`,
  `data-testid="sidebar-matters-link"`), **Vocabulary** (`/vocabulary`,
  `data-testid="sidebar-vocabulary-link"` — directly above Settings, since it
  is data the archive is classified by rather than a display preference, and
  not admin-gated, since `/api/facets/*` is authenticated at include level
  like everything else — see [facets.md](facets.md) §8), **Settings**
  (`/settings`), —
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
  avoid a flash; the seed mirrors this resolution order exactly — primary key,
  then legacy key, then the viewport default — and
  `src/__tests__/sidebar-seed.spec.ts` executes the real script out of
  `index.html` to keep the two in step); when unset it defaults from a `matchMedia('(min-width:1024px)')`
  check. A desktop expand/collapse button toggles between **narrow (icons only)**
  and **wide (icons + text)** at **every** desktop width — the sidebar is no
  longer force-widened at `2xl` (that hid the toggle on large monitors).
- **Mobile:** off-canvas drawer with a `bg-gray-900/30` backdrop; closes on
  click-outside, ESC, or route change.

### `src/components/layout/AppHeader.vue`

Sticky top header. Props `{ sidebarOpen }`, emits `toggle-sidebar` and
`open-search`. Contains: the mobile **hamburger** (`aria-controls="sidebar"`),
the **page title**, a **search trigger** button (`data-testid="header-search-button"`, one of the
modal's entry points), the **`ThemeToggle`**, and a **user menu** showing
`auth.user?.display_name || username` with **Settings** and **Sign Out** (calls
`auth.logout()` then routes to `login`).

**The page title lives here**, not at the top of the page body. It renders as the
page's one `<h1>` (`[data-testid="app-bar-title"]`) immediately right of the
hamburger — the contextual top-app-bar pattern. The *value* stays the view's:
`PageHeader` claims it through the **`usePageTitle`** singleton
(`src/composables/usePageTitle.ts`) and this component only renders what is
claimed. Claims are **token-owned** so a release from a view that no longer owns
the title is a no-op — without that, a route change that mounted the incoming
view before unmounting the outgoing one would blank the bar, and mount ordering
is not something this should depend on. The `<h1>` is `truncate` inside a
`min-w-0` cluster and the right-hand cluster is `shrink-0`, so a long title
yields rather than pushing search/theme/user off a phone. Views with their own
hero title (document detail, note detail) claim nothing and the bar's left side
falls back to just the hamburger.

It previously showed **nothing at all** on the left at `lg+`, while every view
spent ~44px of `#app-page` on an `<h1>` restating the highlighted sidebar item.
That cost most on `/ask`, whose panel is sized off the remaining viewport
(measured: the chat panel gained 45px at 1440×900). What stays in the body is the
view's one-line **description**, restyled as a muted, `max-w-2xl` lede
(`[data-testid="page-lede"]`) — see
[frontend-view-principles.md §1.2](frontend-view-principles.md). A `PageHeader`
given only a title now renders **nothing**, so title-only views (Documents,
Settings, Admin, Recently Deleted, Saved views) start their content directly
under the bar.

`PageHeader` also takes a **`#controls`** slot for the view's filter/control bar
(`[data-testid="page-header-controls"]`, beside
`[data-testid="page-header-actions"]`). `/charts`, `/jobs` and `/matters` pass
their bars through it, so each renders **one** toolbar — controls left, page
commands right — rather than an actions row above a filter row. The merge is
gated on a **container** query (`@5xl`) because the content column is the
viewport minus a collapsible sidebar: at a 1280px viewport the row merges with
the sidebar collapsed and stacks with it expanded, which no `lg:` rule can
express. See [frontend-view-principles.md §5.1](frontend-view-principles.md) and
`e2e/header-toolbar.spec.ts`.

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
| `AppSelect` | GovSelect | `.form-select`, options from `items: SelectItem[]` (`{value, text, disabled?}` — `disabled` is what lets a caller render a stored-but-invalid value truthfully instead of blank, as `ChartRuleEditor` does). Takes `testid` for the inner `<select>`, like `AppInput`. |
| `AppCheckboxes` | GovCheckboxes | `<fieldset>`/`<legend>` + `.form-checkbox` rows from `items: ChoiceItem[]`; Vue-driven conditional reveals via `conditional-<value>` slots; `string[]` model. |
| `AppRadios` | GovRadios | as `AppCheckboxes`, `.form-radio`, scalar model. |
| `AppDateInput` | GovDateInput | **3-field** day/month/year inputs; v-model is an ISO `YYYY-MM-DD` string or `null`. Parse/format logic kept verbatim (no date-picker dependency). |
| `AppBadge` | GovTag | Mosaic pill badge; maps GovTag's `colour` set onto Mosaic `{bg,text}` pairs. |
| `AppPanel` | GovPanel | Violet confirmation panel (title + body slots). |
| `AppDetails` | GovDetails | Native `<details>` disclosure with a violet summary. |
| `AppPopover` | — (new) | Behavioural primitive for the app's dropdown overlays: controlled `v-model:open`, Escape-closes-with-focus-return, outside-mousedown close, one `--z-popover` stacking token. `#trigger` slot (scoped `{ open, toggle, triggerRef }`) + panel default slot; `align` (`left`/`right`/`auto`/`none`) + caller-owned `panelClass`/`panelAttrs`. Anchored **in-flow** (no Teleport), so class-based alignment and the header dropdown's responsive positioning are preserved. Backs `FilterPill`, `DashboardFieldsMenu`, `SaveViewMenu`, the `JobsView` columns menu, and both `AppHeader` dropdowns; `SearchModal` stays a native `<dialog>` (a modal, not a popover). |
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
| `DocumentDetailView` | `/documents/:id` (`document-detail`) | Directly below the **Back to documents** link and above the hero sits a **previous/next document** nav (`[data-testid="doc-neighbors"]`, links `doc-prev`/`doc-next` → the neighbouring `document-detail` routes). Neighbours are computed by the **`useDocumentNeighbors`** composable (`src/composables/useDocumentNeighbors.ts`) and navigate **by document id** — Next → next-higher id (N+1), Previous → next-lower id (N-1), independent of the list sort (stepping in id order reads as intuitive; following the default newest-first sort sent "Next" to an *older*, lower id). There is no server neighbour endpoint and no `id` sort, so it scans `GET /api/documents` **unfiltered** by `added_date desc` (effectively id-descending, since `created_at` and the autoincrement id are both set at insert), paginating (100/page, capped at 20 pages) and reading the nearest ids either side of the current one **numerically** — correct even if two documents tie on `added_date`. It is self-contained (survives a cold deep-link), degrades to no-neighbours on a fetch error, and hides a direction at the ends of the list. The whole bar is hidden while in **review-queue** mode (the queue bar owns navigation there) and for **trashed** documents (excluded from the list, so they have no neighbours). Leads with a full-width **hero header card**: the title (`h1#document-title`) in a **flex row** with an **"Ask about this document"** button (`[data-testid="ask-about-document"]`) — a **primary (violet) `AppButton`** with a chat icon that sits **top-right of the title row on desktop** and **stacks under the title on mobile**. That button opens the Ask view in a **new tab** (`target="_blank"`) at `{ name: 'ask', query: { q: <prompt> } }` — `AppButton` gained a `target` prop that passes through to its `RouterLink`/anchor form and auto-adds `rel="noopener"` for `_blank`. The pre-filled prompt reads `Tell me about the document "<title>" (<kind> from <sender>, <date>): ` — any missing kind/sender/date is gracefully omitted (no empty parentheses), and the title falls back to `original_filename` then a generic `this document`. This is **pre-fill only**: there is no backend change and no document-scoped retrieval — the prompt just names the document so the existing Ask RAG surfaces it (see [ask.md §1.2](ask.md)). Both the hero button and the floating **Action dock** (below) render as real `target="_blank"` anchors sharing one `askHref` computed (`router.resolve({ name: 'ask', query: { q: askPrompt } }).href`), so native new-tab affordances (middle-click, cmd/ctrl-click, "open in new tab") work on either. Once the hero has scrolled off screen — tracked by an `IntersectionObserver` on `#document-hero` — the **`ActionDock.vue`** component mounts (extracted from this view and renamed from the earlier inline "island"): a `sticky` (not `fixed`) full-content-width wrapper (`[data-testid="action-dock-wrapper"]`, `v-if` on the whole component, not `v-show`, so it is fully absent from the DOM while the hero is visible) keeps it inside the page's own scroll container rather than floating over the sidebar, and holds a pill (`[data-testid="action-dock"]`) with the hero's two primary actions: an **Ask** anchor (`[data-testid="action-dock-ask"]`, the same `askHref`/new-tab anchor as the hero button) and an **Edit/Done** toggle (`[data-testid="action-dock-edit-toggle"]`, `aria-pressed`) that flips the *metadata* edit mode. The dock's on-screen position is a per-user preference — one of `top-left` / `top-middle` / `top-right` (default) / `bottom-left` / `bottom-right` — read from `dockPosition` on the auth store and set via **Settings → Appearance → Action dock position** (`PUT /api/settings/appearance`; see [api.md §1.10.3](api.md)); the `top-*` positions carry a `top-16` offset so the dock clears the sticky, fixed-height (`h-16`) `AppHeader` instead of rendering underneath it. The pill row is then inset from that edge (`top-4` / `bottom-4`) so it floats with a comfortable gap rather than squished flush against the header / viewport bottom, and its horizontal padding matches the navbar (`px-4 sm:px-6 lg:px-8`) so a left/right-anchored dock lines up with the header's outermost elements (which span the full content width) rather than the narrower, max-width-capped main column. That edit-mode flag is lifted into a shared singleton composable, **`useMetadataEditMode`** (`src/composables/useMetadataEditMode.ts`, a module-level ref mirroring `useDocumentLayout`'s `editMode` — not persisted, so a reload/navigation never resumes with the editors open); the hero's `[data-testid="edit-toggle"]`, every metadata section tile, and the dock's toggle all read/flip the one flag, so opening the editors from the dock shows exactly the same per-field editors the hero toggle would. This metadata edit mode is **distinct** from **Edit layout** (`useDocumentLayout`, below) — the dock only surfaces the metadata toggle, not layout editing. The hero also carries a labelled **stat row** (Kind · Sender · **Recipient** · Date on document · **Date added to library** · **Last edited** · Amount, plus opt-in Language / Due date / Expiry date hidden by default) rendered from the per-machine `useDocumentLayout` field list (see the **Edit layout** mode in the component-structure section below); in **read mode it shows only stats that are both *visible* in the saved layout and *populated*** (no em-dash filler), while the three dates read as a distinct trio — **Date on document** (the date printed on the document, editable), **Date added to library** (`created_at`, read-only) and **Last edited** (`updated_at`, read-only, bumps on any change incl. tags/projects) — the last two rendered with date+time, the document's **tags as colour-varied `AppBadge` pills** (colour derived from the tag name via `tagColour`, so it's stable across renders), and its **projects as `AppBadge` pills wrapped in `RouterLink`s** (`[data-testid="project-badge"]`, each → `/?project=<slug>` to filter the dashboard to that project). Below the hero, **two columns on desktop**: the **metadata tiles on the left** and the **preview pane on the right**. What used to be one "Details" card is now **one first-class tile per metadata section** — Content (which also holds **Kind + Language**, the former standalone "Classification" tile, since folded in — a two-field panel read as over-fragmented), Sender, recipient & dates, Financial, and a read-only System tile — each rendered from the same `DocumentMetadataEditor.vue` with a `section` prop (its group from `fieldGroups` + `ACCENT`), so each carries an accent-coloured heading and is **independently drag-reorderable** across columns like any other section card. A **value-less tile hides entirely in read mode** (Content and System always show; Sender, recipient & dates / Financial appear only when populated), so a born-digital note shows no empty "Financial"/"Sender, recipient & dates" card at all; **entering edit mode reveals every tile** so anything stays fillable, and within a *present* tile a value-less field still renders with an em-dash. **Topics** fold into the **Content** tile (they describe the document's "aboutness"), not a tile of their own. Fields lay out in a **two-column grid** (`sm:grid-cols-2`; long fields span both columns, and in edit mode every field spans both) with **larger values** (`text-base`) under **smaller uppercase labels** (`text-xs`); Amount renders as an emphasised figure and Status as a coloured pill (`statusAccent`). Editing is a single **page-wide Edit toggle in the hero** (`[data-testid="edit-toggle"]`, label **Edit details**/**Done**, `aria-pressed`, kept visually distinct from the neighbouring **Edit layout** toggle) that flips the shared `useMetadataEditMode` flag **every section tile reads** — *not* a per-tile toggle and *not* the old per-row "Change" buttons. Toggling on reveals an inline `App*` editor for **every** field at once (with a "changes save automatically as you leave each field" hint); each field **autosaves independently** the moment its edit commits (native `change` bubbles from the input to the field wrapper; text fields also commit on Enter), PATCHing **only that field** via the existing per-field endpoint. There is **no global Save/Cancel** — "Done" just leaves edit mode. A dirty-check guards against needless PATCHes, a server-canonicalised value (e.g. slugified tags) is re-synced back into the editor, a brief green **"Saved"** indicator appears per field (`[data-testid="saved-<field>"]`), and validation/save errors show **inline per field** (`fieldError[field]`). Rows keep their `row-<field>`/`row-value` hooks; the **Tags** row (`#edit-tags`, in the Content group) is a **token multiselect** (`AppMultiSelect`) over tag **slugs** — selected tags show as removable chips, an input filters existing tags (from the shared taxonomy cache) into a menu, and typing a slug that matches nothing offers a **"Create …"** option; every add/remove autosaves the full-replacement slug list and refreshes the taxonomy cache, and read-mode tags render as `AppBadge` chips linking to the tag-filtered dashboard (`[data-testid="tag-badge"]`, `/?tag=<slug>`). The **Projects** row (`#edit-projects`, in the Content group) is a **token multiselect** (`AppMultiSelect`): selected projects show as removable chips, an input filters existing projects (from the shared taxonomy cache) into a menu, and typing a name that matches nothing offers a **"Create …"** option — every add/remove autosaves the full-replacement `projects` list via PATCH (unknown names upserted server-side), then refreshes the taxonomy cache so a newly created project is offered everywhere. Within the **Sender, recipient & dates** group, the **Recipient** field — like Kind — is an `AppSelect` dropdown ("Not set" + every known recipient, options from the shared taxonomy cache via `GET /api/recipients`) with an inline **"Add recipient…"** affordance (a sentinel option that reveals a text input to name a brand-new recipient without leaving the page); both paths PATCH `{recipient: <name>|null}`, upserted case-insensitively server-side, and after an inline add the dropdown options reload so the new name appears. The System group's **OCR confidence** (`[data-testid="ocr-confidence"]`) shows the engine score as a percentage, or — when null — distinguishes provenance: **"Imported (Paperless) — text layer reused from Paperless — no OCR re-run"** when `source === 'import'`, otherwise **"Not applicable — born-digital text — no OCR run"** (born-digital PDFs and plain-text uploads skip OCR, so no confidence is recorded — see [ingestion.md](ingestion.md)). The refined per-page **markdown** is a **first-class reader card** ("Document text", `[data-testid="markdown-content"]`) **eagerly fetched on load** from `GET /api/documents/{id}/markdown` (no longer a collapsed `View markdown` disclosure): pages render continuously with `marked` + `DOMPurify`, a "Page N" separator only when `page_count > 1`. For a born-digital `.md`/`.txt` with no PDF/image, this reader **is** the primary pane — the "No preview is available" fallback is suppressed when readable text exists (`hasReadableText`). The raw `ocr_text` is not surfaced in the UI (it still backs full-text search). The preview card has a slim **header bar** with **Open** (new tab → inline URL) and **Download** (attachment → searchable PDF when present, else original) buttons, so the document window itself stays chrome-free. PDF rendering is handled by **`DocumentPdfPreview.vue`** (`pdfjs-dist`): pdf.js decodes the file in a Vite-bundled worker and renders every page to a `<canvas>`, scaled to fit the container width (`devicePixelRatio`-aware). Pages load lazily via `IntersectionObserver` (300 px root-margin look-ahead), so only visible pages are rendered; a faded first-page thumbnail (`poster` prop, when a thumbnail exists) is shown while loading. This produces identical output across Chrome, Firefox, and Safari — there is no native viewer chrome, no per-engine toolbar quirk, and no UA-sniffing. Three fallback states: **loading** (spinner + optional poster), **password** (padlock icon + Open link), and **error** (Open + Download links). Stacks on mobile, preview first; both columns are `min-w-0` and text containers `break-words` so long titles/values wrap rather than widen the page (which made iOS Safari zoom in). **Deleted (trash) documents:** the view fetches with `getDocument(id, { includeDeleted: true })`, so a title click from **Recently Deleted** opens a soft-deleted document **read-only** instead of 404ing. When the loaded document has `deleted_at` set, a red **trash banner** (`[data-testid="trash-banner"]`) renders above the hero with **Restore** (`[data-testid="trash-restore"]` → `POST .../restore`, clears the banner) and **Delete permanently** (`[data-testid="trash-purge"]`, opens a `ConfirmDialog` → `DELETE .../permanent`, then routes back to `/deleted`); the ordinary soft-delete link is hidden while deleted. **Live status:** watches `jobsStore.lastEvent` and, on an event for *this* document, refetches it (`getDocument`) so the Status pill and any pipeline-filled metadata refresh without a reload — suppressed while a re-extraction poll is running, which owns refreshes then. A **Comments** card (`DocumentComments.vue`, `[data-testid="document-comments"]`) sits in the metadata column, by default after the metadata section tiles and before Actions: an "Add a comment" `AppTextarea` + submit button, then a newest-first list of existing comments (each showing its dated `created_at` timestamp — a comment's recorded date, not the document's own date — plus per-comment **Edit**/**Delete** controls, `[data-testid="comment-item-{id}"]`/`comment-edit-{id}`/`comment-delete-{id}`); at most one comment is in edit mode at a time (singleton `comment-edit-body`/`comment-edit-save`/`comment-edit-cancel` testids, mirroring `NoteEditorPanel`'s pattern). A comment is a NEW concept, distinct from a note: a note is its own `source='note'` Document, while a comment is user-authored dated text attached to an *existing* document via `POST`/`PATCH`/`DELETE /api/documents/{id}/comments[/{cid}]` ([api.md §1.19](api.md)); every add/edit/delete re-fetches the parent document (`@changed`) and queues a re-embed so `/ask` can find the document through its comments. The card is registered as card id `'comments'` in `useDocumentLayout`'s `DEFAULT_CARD_COLUMNS` (in the left/metadata column by default; see the component-structure section below), so it participates in **Edit layout** show/hide + free-form drag like any other section card. The Actions card has a **View job history** link (`[data-testid="view-job-history"]`) → `/jobs?document_id=<id>` (the "Ask about this document" button now lives in the hero, above). Below it, a **History** card (`DocumentHistoryTimeline`, `[data-testid="document-history"]`) renders the document's `events` audit trail as a **reverse-chronological** timeline of **humanized milestones** (newest first — the most recent event sits at the top; equal timestamps keep their incoming order via a stable sort) (Ingested, OCR complete, Description & metadata added, Indexed for search, Edited, Projects changed, …). It is meant to be the **self-sufficient record of how the document was processed**, so processing-relevant steps carry a small breakdown rather than a bare label: the **extraction** milestone (`extraction_completed`) shows a **method sentence** describing how the input was sent — distinguishing normal OCR-text extraction, the "OCR was unusable → original file sent" case, the **model-only escalation**, and, given a **violet-accented** unmissable line, the **vision fallback** (`[data-testid="history-extraction-method"]`, when the low-confidence retry re-read the original file — `escalated` + `input_mode` ∈ {document, image}) — plus a wrapped row of small labelled **chips** (`[data-testid="history-extraction-chip"]`: model, confidence, and cost). An **`extraction_skipped`** step is now surfaced as its own milestone (labelled "Extraction skipped") with its reason (budget skips show spent-of-budget; input/file skips show the detail string), and **failure** steps (`extraction_failed` / `ocr_failed` / `markdown_failed` / `embedding_failed`) surface their carried error message (`[data-testid="history-secondary"]`). The noisy per-stage `status_changed` transitions and the low-signal `*_skipped` events (`embedding_skipped`, …) remain hidden by default; only `extraction_skipped` graduates to a milestone. A **"Show all events"** disclosure (`[data-testid="history-show-all"]`) still reveals the complete raw log (including raw token counts, which stay out of the curated view). **Topics** (the auto-extracted subject phrases) render as **read-only** colour-varied `AppBadge` pills (`[data-testid="topic-badge"]`) inside the **Content tile** (below its fields, shown whenever non-empty) — there is **no topics editor** (topics are owned by extraction and indexed for search; see [api.md §1.5](api.md)); only `tags` remain editable. **Notes** (`source === 'note'`) get a dedicated surface instead of the generic per-field editor: a page-wide note **Edit** toggle reveals a markdown-body draft (no separate title field — the title is the body's first line via `deriveNoteTitle`) with the **same edit / split / preview view-mode toggle** as the note-create view (shared via the `useMarkdownEditorMode` composable and the `library:note-editor-mode` storage key, so the preference is global) and a live sanitised preview pane (`[data-testid="note-edit-preview"]`), that `PATCH /api/notes/{id}` in place (`updateNote`, sending `{title: deriveNoteTitle(body), body_markdown}`), and a collapsible **version-history** panel (`listNoteVersions`) lists each snapshot with a per-version **Restore** action (`restoreNoteVersion`) — see [api.md §1.17](api.md). |
| `DocumentDeleteView` | `/documents/:id/delete` (`document-delete`) | A confirmation page (its own URL, not a JS modal) with a destructive `AppButton` + `AppBackLink` cancel. |
| `RecentlyDeletedView` | `/deleted` (`documents-deleted`) | The **Recently Deleted** holding area: an `.app-doc-grid` mosaic of soft-deleted documents (`GET /api/documents/deleted`), each tile (`[data-testid="doc-card"]`) showing the title (→ detail), kind·sender, the deleted date, and a countdown (`[data-testid="purge-countdown"]`, "Purges in N days" / "Purges soon" at 0). A per-tile **Restore** button (`[data-testid="restore-<id>"]`) calls `POST /api/documents/{id}/restore`, removes the card, and shows a success `AppBanner` (`[data-testid="flash-banner"]`). A per-tile **Delete permanently** button (`[data-testid="purge-<id>"]`) opens a `ConfirmDialog`; confirming calls `DELETE /api/documents/{id}/permanent`, removes the card, and flashes. The title links to the detail route, which opens the deleted document **read-only** (the detail view fetches with `?include_deleted=true` and shows a trash banner) rather than 404ing. Loading / error / empty (`[data-testid="deleted-empty"]`) states; an intro line names the `retention_days` window. Reached from the sidebar **Recently Deleted** link (`[data-testid="sidebar-deleted-link"]`). See [api.md §1.6](api.md). |
| `UploadView` | `/upload` (`upload`) | `AppFileUpload` drop-zone (`accept` covers images, PDF, and **text/markdown notes** — `.md`/`.markdown`/`.txt`); each file uploads independently with its own `AppProgressBar`, then polls until `indexed`/`failed`; duplicate/error states via `AppBanner`/`AppErrorSummary`. |
| `NewNoteView` | `/notes/new` (`note-new`) | In-app **note authoring**: a `#new-note-form` with an `AppTextarea` markdown body (`#note-body`) and a **live preview** (`[data-testid="note-preview"]`) rendered through the same `marked` + **DOMPurify** sanitise pipeline as the detail-view reader. The "first line becomes the title; Markdown supported" guidance lives in the **`PageHeader` description** (not a hint above the editor), so the edit and preview panes start at the same top edge and stay vertically aligned. An **edit / split / preview** view-mode toggle (Split is the default; the wide-only Split button hides on narrow screens) controls which panes show — sourced from the shared **`useMarkdownEditorMode`** composable (`src/composables/useMarkdownEditorMode.ts`) and persisted per-machine under the `library:note-editor-mode` storage key, so the same preference drives the in-place note editor in `DocumentDetailView` (a display-size preference — [frontend-view-principles.md](frontend-view-principles.md) §4). There is **no separate title field** — the **title is the first line of the body** (`deriveNoteTitle` in `src/utils/noteTitle.ts`: first non-empty line, leading markdown heading marker stripped, capped at 200 chars). Save (`#note-save`, disabled until that first line is non-empty) `POST`s `{title: deriveNoteTitle(body), body_markdown}` to `/api/notes` (`src/api/notes.ts`) and routes to the new note's `document-detail`; API failures surface in `AppErrorSummary`. Reached from the sidebar **New note** link (`data-testid="sidebar-notes-link"`). |
| `AskView` | `/ask` (`ask`), `/ask/new` (`ask-new`), `/ask/:threadId(\d+)` (`ask-thread`) | **Two-screen, route-driven chat** (Option B; see [ask.md §1.6](ask.md)). The visible **mobile** screen follows the route: `ask` → the conversation **list** (full screen — a right-aligned ＋ that routes to `ask-new` (`[data-testid="ask-new-mobile"]`) — the only way into a chat on a phone, since the rail's "New conversation" button is `max-lg:hidden`, `[data-testid="thread-search"]`, and the thread list); `ask-new`/`ask-thread` → the **chat** screen (`[data-testid="ask-thread-pane"]`) with a back arrow (`[data-testid="ask-back"]` → `ask`), a title bar (`[data-testid="ask-thread-bar"]`) showing the thread title + a ⋯ menu, the transcript, and a composer **pinned to the bottom**. At **`lg+`** both panes show side by side (rail \| thread) and the route only sets the active thread; the view's `PageHeader` is desktop-only (`max-lg:hidden`), which hides its **lede** below `lg` but *not* the page title — that is claimed for the app bar and shows at every breakpoint (see the `AppHeader` section), which is why the mobile list screen no longer carries an "Ask" heading of its own. The `:threadId` param is digit-constrained and `/ask/new` is declared before it, so `new` is never parsed as an id. Thread rows and the chat title bar both expose a **⋯ overflow menu** (`ThreadActionsMenu`, `[data-testid="thread-actions-menu"]` / `ask-title-actions`) with **Rename** (`[data-testid="thread-rename"]` → inline title input + Save/Cancel, Enter saves / Esc cancels, blank-or-unchanged is a no-op) and a two-step **Delete** (`[data-testid="thread-delete"]` → Confirm/Cancel); deleting the active thread from the title bar returns to the list. "New conversation" (desktop button / mobile ＋) routes to `ask-new`; the desktop button is greyed out when already an empty new conversation. A `/ask?q=…` deep link (the document detail "Ask about this document" button) is redirected to `/ask/new?q=…` so the seed lands on the chat screen where the composer lives. Each turn is **visually layered**: the question is a right-aligned violet bubble, the answer is sanitized markdown (`#ask-answer`, `marked` + `DOMPurify`, GFM **tables**), citations **collapsed by default** behind an `AppDetails` disclosure ("Citations (N)") opening to citation `RouterLink`s, and a `used_tools`/`cost_usd` meta line. On mobile the chat is a **fixed-height flex column** (`chatFillClass`: `h-[calc(100dvh-4rem)]`, `-my-8` to cancel `#app-page`'s `py-8`) that fills the viewport below the `h-16` header: the transcript is the **internal scroll area** (`max-lg:flex-1 max-lg:overflow-y-auto`) and the composer is a **footer** (`shrink-0`; `lg:sticky` only at desktop), so it docks at the bottom / above the on-screen keyboard (`100dvh` + the `interactive-widget=resizes-content` viewport meta + `env(safe-area-inset-bottom)`) instead of floating on a sticky that only pins on overflow. It is also **full-bleed** — `#ask-page` drops its card border/rounding and breaks out of the shell's side padding — and each turn is **flat** (violet question bubble over plain answer text); the shaded, bordered answer card (`[data-testid="ask-answer-surface"]`) is **`lg:`-gated**. The composer (`[data-testid="ask-form"]`) is a **single flat full-width bar** — the `form` element itself *is* the text-entry surface (`bg-gray-100 dark:bg-gray-900/40`, square-cornered apart from `lg:rounded-b-xl` following the panel), with **no pill or boxed field nested inside it**; the earlier `rounded-3xl` pill sitting inside a bordered white footer read as a box within a box. Its `border-t` turns violet on `focus-within`, and since the textarea carries `focus:outline-none focus:ring-0` **that rule is the composer's only visible focus indicator**. Inside: a borderless auto-growing `<textarea>` `#ask-question` with zero horizontal padding (the form owns the `px-3 sm:px-6` gutters, matching the transcript's, so the placeholder lines up with the answers above), then the **attach paperclip** (`[data-testid="ask-image-attach"]` → hidden `ask-image-input`, up to 5 base64 thumbnails `[data-testid="ask-image-preview"]`, remove button — W11) and the **Send** `AppButton` `#ask-submit` (a live **Stop** while answering) on their **own row below the text**, so the text field is full width and the controls never squeeze it → `POST /api/ask` (`src/api/ask.ts`). `onComposerKeydown`: **Cmd/Ctrl+Enter** always sends and **Shift+Enter**/**Ctrl+J** insert a newline; plain **Enter** sends at `lg+` but inserts a **newline below `lg`** (phone — send is the button's job); Enter mid-IME-composition never sends. A new chat shows a greeting + example-prompt buttons (`[data-testid="ask-greeting"]` / `ask-example-prompt`, which fill the composer); with threads but none selected it prompts to pick one (`[data-testid="ask-select-thread"]`); with none it invites a first question (`[data-testid="ask-empty"]`). Errors (notably the 503 "no API key" case) surface in `AppErrorSummary`. A **successful answer never shows an error**: the post-success side effects (record `thread_id`, replace the URL with `/ask/:threadId`, refresh the sidebar) run in `syncThread` *outside* the answer-error `catch`, and a missing/non-numeric `thread_id` or a Vue Router rejection is logged rather than turned into a spurious alert. See [ask.md §1.6](ask.md). |
| `SettingsView` | `/settings` (`settings`) | Tabbed settings (`role="tablist"`). **Dashboard** tab: the shared **`DashboardFieldsEditor`** (checkbox per field **plus drag/Up-Down reorder** and "Reset to defaults" — the same component the dashboard "Fields" popover uses, see §1.5), with an explicit **Save changes** → `PUT /api/settings`. **Appearance** tab: page-canvas tone swatches (`BACKGROUND_TONES`) **and** a document-tile preview choice (`TILE_PREVIEWS`: full-width top crop, the default, vs whole-page letterbox), both applying live and auto-saving per click → `PUT /api/settings/appearance` (optimistic store update so the canvas/tiles repaint instantly; reverts on failure). The same tab also has an **Action dock position** card (`[data-testid="settings-dock-position"]`, `role="radiogroup"`): five buttons — `dock-position-top-left` / `dock-position-top-middle` / `dock-position-top-right` (default) / `dock-position-bottom-left` / `dock-position-bottom-right` — choosing where the document-detail page's floating **Action dock** (§1.5, `DocumentDetailView` component structure) appears; picking one auto-saves the same way, optimistically updating `auth.dockPosition` → `PUT /api/settings/appearance`. A **Phone columns** card (`[data-testid="settings-phone-columns"]`, `role="radiogroup"`, buttons `phone-columns-1`/`phone-columns-2`/`phone-columns-3`) sets how many dashboard tile columns render on phone-width screens (`< 641px`; default **2**, was 1 before this preference existed — existing users' phones flip to 2 columns) — a server-synced account preference (`phone_columns` on `auth.phoneColumns`, stored in the `user.preferences` JSON blob, no DB migration) driving the `--doc-grid-cols-phone` CSS var read by `.app-doc-grid` (§1.4); the tablet band stays fixed at 2 and the desktop `--doc-grid-cols` "tiles per row" control (a separate, `localStorage`-only preference) is unaffected. Picking a count auto-saves the same optimistic way → `PUT /api/settings/appearance`. A **Tile description on mobile** card (`[data-testid="hide-summary-mobile"]`, a `.form-checkbox`) toggles `hide_summary_mobile` (on `auth.hideSummaryMobile`, default off) — a server-synced account preference that, when on, removes each dashboard tile's description on phone-width screens (`<= 640px`) via a reactive `v-if` in DocumentListView (§1.4); larger screens always show it. Toggling auto-saves the same optimistic way → `PUT /api/settings/appearance`. Also a **Document type colours** card: one row per kind (loaded via `GET /api/kinds`, ordered most-used first) with a native colour picker, one-click `SUGGESTED_COLORS` swatches, a per-kind **Default** reset and a **Reset all**, saving the sparse override map → `PUT /api/settings/kind-colors` (optimistic; `resolveKindColor` in `utils/kindColor.ts` resolves override → `DEFAULT_KIND_COLORS` → neutral). All three surface success/error via `AppBanner`/`AppErrorSummary`. **Ask** tab (`[data-testid="tab-ask-btn"]` / panel `tab-ask`): one **About you** card — a free-text `AppTextarea` (`ask-profile`, seeded from `auth.askProfile`) for facts the documents never state (who lives with the user, current address, whose car, employer) that the Ask system prompt carries on every turn as authoritative personal context ([ask.md §1.2, *Archive context*](ask.md)). Free text, so an **explicit Save** (`form[data-testid="ask-profile-form"]`) rather than the Appearance tab's save-per-click — typing never saves, and a half-typed note never reaches the prompt. Saves with `PUT /api/settings/ask-profile` ([api.md §1.10.11](api.md)), mirrors the echoed value onto the store, and shows `ask-profile-saved`; a failure shows `ask-profile-error` (a 422 names the character cap, `ASK_PROFILE_MAX_CHARS`) and keeps the typed text. **Email triage** tab (`[data-testid="tab-email-triage"]`): a **read-only** view of the instance-wide email-in triage pipeline — fetched **lazily on the tab's first show** from `GET /api/settings/email-triage` ([api.md §1.10.6](api.md)). Shows a **Hold pipeline ON/OFF** badge (`email-triage-hold-master`), a "view held emails" link to `/held-emails`, the poll interval / Held & Processed folders / IMAP timeout, and the five-step decision flow as an ordered list — sender allowlist (accept-all vs N allowed senders + unknown-sender hold badge), noise gate (+ tiny-image thresholds and the decoration-image signal ceilings, `triage-decoration-thresholds`), LLM verdict (Active / Inactive — distinguishing "no API key" from "disabled by configuration"; model, daily budget, prompt version, and the fail-open note), body substance gate (word/char thresholds + below-substance hold badge), and the nothing-ingested hold — each step with live values and an `AppBadge` where a switch exists. Below the flow, a **"Recently skipped items"** card (`triage-recent-skips`, fed by `GET /api/settings/email-triage/recent-skips` [api.md §1.10.7](api.md), loaded alongside the config): the last 20 emails with a filtered/dropped item, each row showing subject/sender/time plus its per-item skip reasons — the first place to look when a forwarded attachment seems to have vanished. Its load is best-effort: a failure shows an unavailable note (`triage-recent-skips-error`) instead of a false "no skips", and never blanks the config view. Nothing is editable (settings are environment-only; the footnote cites `docs/runbooks/email-triage.md`); when `email_in_configured` is false a single "Email-in is not configured on this server" empty state (`email-triage-unconfigured`) replaces the flow. Semantics: [ingestion.md](ingestion.md), "Email item selection" / "Held for review". **Notifications** tab (`[data-testid="tab-notifications-btn"]` / panel `tab-notifications`): the per-user push/forward preferences — a master `enabled` switch, Pushover app token / user key (write-only: the read model returns only `pushover_app_token_set` / `pushover_user_key_set`, and sending "" keeps the stored secret) and optional device, a checkbox per notifiable event (`NOTIFICATION_EVENTS`: document processed, processing failed, needs review, duplicate, email held), and a list of email-forward addresses — saved with `PUT /api/settings/notifications` and mirrored onto `auth.notificationSettings`. Credentials are validated at save time, so a bad token is a `422` rather than a push that silently never arrives. **LLM backend** tab (`[data-testid="tab-llm-backend-btn"]` / panel `tab-llm-backend`): the **instance-wide** choice of how each LLM surface reaches Claude — metered API vs Claude subscription — fetched **lazily on the tab's first show** from `GET /api/settings/llm-backends` ([api.md §1.10.8](api.md)); the narrative is in [llm-backends.md](llm-backends.md). A **How Claude is reached** card shows an `AppBadge` for whether an Anthropic API key is configured (`llm-api-key-status`) and the sentence-cased subscription credential status (`llm-credentials-status`) — both colour-coded via AppBadge's **`colour`** prop (green healthy / yellow degraded / red unhealthy). Note `colour`, not `variant`: AppBadge ignores an unknown prop silently, so the first version rendered every badge grey and a text-only test did not catch it with its human-readable detail (`llm-credentials-detail`) — so an admin can tell *before* switching whether the target backend would work. A **Per-feature backend** card renders one row per surface returned by the API — today that is one, `llm-surface-ask` (`llm-surface-series_insight` disappeared with the series stack; the card is still driven off the list, not hard-coded to a single row): label, description, a plain **"Changed here"** badge (`llm-overridden-<surface>`) when a stored value differs from the environment, a `<select>` (`llm-backend-select-<surface>`) and, only for an overridden surface, a **Reset to <default label>** button (`llm-reset-<surface>`, e.g. "Reset to Metered API") → `DELETE /api/settings/llm-backends/{surface}`. The button names the value it reverts to rather than saying "deployed default", so the badge does not have to restate it in jargon. Changing the select saves immediately → `PUT /api/settings/llm-backends/{surface}`, per-surface (`llmSaving` is the surface id, not a boolean, so one row saving does not freeze the others). Read-only for non-admins: the payload's `editable` flag disables every control and shows `llm-readonly-note`, rather than letting a non-admin discover a 403. On a rejected change (`409` — e.g. subscription selected with no credentials provisioned) the tab re-reads the stored state so the control reverts to what is actually saved, **then** surfaces the server's `detail` verbatim in `llm-backend-error` — the message names the command to run on the host, so replacing it with a generic string would throw away the only actionable part. |
| `SpendingBoardView` | `/charts` (`charts`) | **The spending board.** One `SpendingCard` per saved `Chart`, ordered by `ordinal` then name — never by document count, which lives on a card's *data* (`ChartData.documents`), not on `Chart` itself, and must never leak into sort order. Reorder is one function, two triggers (spec §4.2): a card's overflow-menu **move up** / **move down**, and whole-card drag via SortableJS (the `DashboardFieldsEditor.vue` pattern); only the ordinals that actually moved are `PATCH`ed. Every chart's data loads in parallel (`Promise.allSettled`) so one chart's fetch failure renders inline on that card (`SpendingCard`'s own `error` prop) rather than as a page banner hiding the ones that loaded; a card whose first load hasn't resolved shows a skeleton (`spending-card-placeholder`) instead of an empty rectangle. The header's `#controls` slot holds **`QuestionDraft`** (free-text "ask a question" → `POST /api/spending/draft` → save) and a `CurrencySelect` that sets the **board's own display-currency preference** (`library:charts-board-currency`, per-machine, defaults to `useCurrencyOptions()[0]`) — the currency new charts, including the empty state's "All spending", are created in. Zero charts renders **`SpendingEmptyState`** instead of the grid. Reachable from the sidebar **Charts** link (`[data-testid="sidebar-charts-link"]`). See "Spending board and workspace" below for the component set and the container-query geometry. |
| `SpendingWorkspaceView` | `/charts/:chartId(\d+)` (`spending-workspace`) | **One saved chart, examined in full**: a toolbar (grain / split / date range / currency), an **Edit rule** trigger (`workspace-edit-rule`), the stacked `SpendingChart`, `SpendingLegend`, the `SpendingFooter` accounting statement, and `SpendingDrillPanel` (opened from a bar, a folded "Other" segment, or a footer bucket). The editor trigger is in `PageHeader`'s `#actions` slot, **not** in `workspace-toolbar` — that element is `hidden` below `@3xl/workspace`, and editing what a chart matches has to be reachable at every width. This is the only entry point: there is none on the board card. Fetches the chart once by id (`fetchChart`) and re-fetches `/data` on any toolbar change; the date range is sent to the API (`from`/`to`) rather than clamped client-side, so the headline and the drawing can never disagree. `split` is always sent **explicitly** — the API reads an omitted `split` as "use the chart's default" and `split=` (empty) as "no split axis", so turning the split control off must send the empty string, never drop the key. A rule edit is the one mutation that does **not** flow through that toolbar watcher: `currentArgs` is built from grain / split / currency / from / to, and a rule change moves none of them — so `onRuleSaved` replaces `chart.value` and calls `loadData()` **explicitly**, and deliberately does **not** call `initControlsFromChart`, which would reset `from`/`to` and grain and silently discard the range being looked at. It stops the args watcher around the mutation so re-deriving `splitValue` cannot race that reload. Both halves are pinned by tests that were observed failing under the obvious wrong version. Legend isolation/exclusion is a client-side display filter only — the headline always reads `data.total` untouched. While a refetch is in flight the previous render stays on screen, dimmed (`data-busy`), rather than flashing: `SpendingChart` never keys its `<Bar>` on `data`. Its `\d+` constraint is what keeps `/charts/anything-else` from resolving here; it no longer competes with a sibling, the `/charts/legacy` and `/charts/:seriesId` routes having been deleted with the series stack on 2026-08-31 (`router/__tests__/spending-routes.spec.ts` pins both as unresolvable). See "Spending board and workspace" below for the container-query geometry that picks the toolbar's and drill panel's presentation. |
| `VocabularyView` | `/vocabulary` (`vocabulary`), `/vocabulary/:facetKey/:valueKey/merge` (`vocabulary-merge`) | The client for the facet CRUD, colour and suggestion-queue routes ([facets.md](facets.md) §8) — authenticated but **not** admin-gated, matching `/api/facets/*`. A `role="tablist"` shell (local `tab` ref, no sub-routes, the pattern `AdminView.vue` already uses) over three `v-show`n panels — Facets (`FacetsPanel.vue`: rename/alias/merge/delete/colour, plus creating a facet or a value), Senders (`SendersPanel.vue`: a sender's split colour only — renaming/merging/deleting one stays in `/admin`'s taxonomy panel), Suggestions (`SuggestionsPanel.vue`: accept/dismiss the pending queue). Each panel loads **lazily on first activation**, not on mount: `watch(() => props.active, ..., { immediate: true })` with a `loaded` flag, the same shape `AdminMetadataPanel` uses — `{ immediate: true }` is the one deliberate deviation from that literal pattern, added because the Facets tab is active the instant `VocabularyView` mounts, which the literal (non-immediate) form would never fetch for. **Merge** (`ValueMergeView.vue`, the second route) is a full confirmation page rather than a modal — the GOV.UK destructive-action convention `router/index.ts` states on document-delete — whose Apply button is gated on a dry-run preview that is **target-specific**: selecting a new target in the `<select>` nulls the previous count synchronously, before the new `POST .../merge?dry_run` resolves, and a stale response for an already-superseded target is dropped rather than allowed to attach (`if (target.value !== next) return` inside the watcher) — without that guard a slow response for a target the owner has since changed away from could silently re-enable Apply against the wrong target. Of the rendered diff, only the moved-document count is server-sourced; the alias and colour-loss lines are computed from the vocabulary already loaded client-side (`facets.md` §8). The Facets tab's value row uses a **container query**, not a viewport one (`@container` on the card, `@md:flex-row` on the row) — the row sits inside a card inside the viewport-minus-sidebar content column, so a viewport breakpoint would answer for a width the row is never actually rendered at; see [frontend-view-principles.md §5.1](frontend-view-principles.md) for the canonical statement of why, and `e2e/vocabulary.spec.ts`'s three-viewport-project run for where this one is proved. |
| `ProjectsListView` | `/projects` (`projects`) | **Projects index**. The `PageHeader` carries a one-sentence lede (`[data-testid="page-lede"]`) saying what a project is for — a collection you put documents into yourself — since the bare title and a list of names explains nothing to a first-time visitor. Lists every project (`GET /api/projects`) as a card (`[data-testid="project-row-<slug>"]`) with its **document count** (`project-count-<slug>`) and a **name link** (`project-link-<slug>` → `/?project=<slug>`, the project-filtered dashboard); archived projects show an **Archived** badge. A **Show archived** toggle (`project-archived-toggle`) re-fetches with `?include_archived`. **Admins** additionally get management controls (all backed by the admin-only projects endpoints, refreshing the shared taxonomy cache after each mutation): a **+ New project** form (`project-new-button` → `project-create-form` with name + optional description → `POST /api/projects`), per-row **Edit** (inline rename + description → `PATCH`), **Archive/Unarchive** (`project-archive-<slug>` → `PATCH {archived}`), and a **two-step Delete** (`project-delete-<slug>` reveals `project-delete-confirm-<slug>` → `DELETE`; no blocking dialog). Non-admins get the read-only list. Reachable from the sidebar **Projects** link (`[data-testid="sidebar-projects-link"]`). See [api.md §1.16](api.md). |
| `MattersListView` | `/matters` (`matters`) | **Matters index**. As on `/projects`, the `PageHeader` lede states what a matter is for — an evergreen subject documents are filed into automatically — in one sentence. Lists every business matter (`GET /api/matters`) as a row (`[data-testid="matter-row-<slug>"]`) with its **document count** (`matter-count-<slug>`) and a **name link** (`matter-link-<slug>`). Mirrors `ProjectsListView`. The route is open to every authenticated user; the create/rename/archive/delete affordances (`matter-new-button`, `matter-archive-<slug>`, `matter-delete-confirm-<slug>`) are `v-if="isAdmin"` and the write endpoints are `require_admin`. A **Show archived** toggle (`[data-testid="matter-archived-toggle"]`) sits in the `PageHeader` **`#controls`** slot beside the create button, rather than in a band of its own below the header. Empty state: `matters-empty`. |
| `SavedViewsView` | `/saved-views` (`saved-views`) | **Manage saved views**: lists the caller's saved views (`GET /api/saved-views`, via the `savedViews` store) as rows (`[data-testid="saved-view-row"]`) with **Apply** (navigate home with the saved query), inline **rename** (`rename-view-<id>`), **pin/unpin** toggle (`toggle-pin-<id>`, `PATCH {pinned}` — pinned views become sidebar dashboards), two-step **delete** (`delete-view-<id>`), and **up/down reorder** (`view-up-<id>`/`view-down-<id>`, sends the full reordered id list to `POST /api/saved-views/reorder`). Empty state (`saved-views-empty`). Reached from the homepage **Saved views** button (`[data-testid="manage-saved-views-link"]` → `/saved-views`, in the dashboard controls row beside the Save-view / Fields menus) — there is no longer a sidebar link. Views are created from the homepage **Save view** popover (`SaveViewMenu.vue`, `[data-testid="save-view-menu"]` beside the card-fields menu) which serialises the current `buildDocumentQuery(applied)` state; pinned views render as **first-class sidebar links** (one `sidebar-dashboard-<id>` RouterLink each → `/` with the saved query) directly under the **Documents** entry — no "Saved views" heading, no separate subsection. See [api.md §1.20](api.md). |
| `JobsView` | `/jobs` (`jobs`) | Background-jobs dashboard: a **single ordered table** — active (queued/running) rows sort to the top and carry a spinner, finished rows follow in the server's order — one row per document (the server collapses a document's jobs to its latest — [api.md §1.8](api.md)). **Document-less system rows** (e.g. the email poll) render a grey **`System` chip** + humanised task name in the Document cell (`[data-testid="jobs-system-label"]`) instead of empty em-dashes. A **filter bar** (`[data-testid="jobs-filter-bar"]`) rides in the `PageHeader` **`#controls`** slot, sharing one toolbar row with **Show system tasks** and **Columns** rather than opening a band of its own. It offers a **task-type** `<select>` (`[data-testid="jobs-task-filter"]`, options from `GET /api/jobs/task-names`) and a **document typeahead** (`#jobs-document-filter` / `[data-testid="jobs-document-filter"]`, searches `GET /api/documents?q=`, guidance in its placeholder). Both are raw `.form-select` / `.form-input` + `.filter-label` rather than `AppSelect`/`AppInput`: those carry the stacked-form label, which would clash with the header's own controls now that they share a row ([frontend-view-principles.md §5.1](frontend-view-principles.md)). Choosing a document switches the server to **history mode** (every job for it, newest first — the heading becomes **History**) and shows a removable chip (`[data-testid="jobs-document-chip"]`). Both filters live in the **URL query** (`?task=&document_id=`), so `/jobs?document_id=<id>` deep-links a document's history (the detail page's **View job history** link). A **Columns** menu toggles per-column visibility (persisted to `localStorage`). **Live updates:** the view watches `jobsStore.lastEvent` and refetches on *every* document event (catching intra-pipeline stage changes that leave `activeCount` unchanged); while **Show system tasks** is on it also polls every 10 s, since system tasks emit no SSE event. |
| `HeldEmailsView` | `/held-emails` (`held-emails`) | The **hold-for-review queue**: emails the ingest pipeline held instead of filing (semantics in [ingestion.md](ingestion.md), "Held for review"; endpoints in [api.md §1.21](api.md)). A status filter (`[data-testid="held-emails-status-filter"]`: Held *(default)* / Ingested / Dismissed / All) over rows (`held-email-row`) showing sender/subject/date, a **verdict chip** (`held-email-verdict`) + reason line, and a lazy-expanded **structured per-item decision trace** (loaded via GET detail; parallel markup to `DocumentHistoryTimeline`'s "Email triage" breakdown — one line per item: `filename ?? '<body>'` → stage → verdict (reason), plus From/Subject chips). Row actions: **Ingest anyway** (queues the override task; the row shows a queued state while the `heldEmails` store polls it to resolution) and **Dismiss** (DB-only, immediate). Resolved rows show the outcome, links to any created documents, and `last_error`. Fed by `src/api/heldEmails.ts` + `stores/heldEmails.ts`. Reached from the dashboard's held-emails affordance (below) or directly. |
| `AdminView` | `/admin` (`admin`, `meta.adminOnly`) | **Admin-only** (the `authGuard` redirects non-admins to `/`; the sidebar link is hidden unless `auth.isAdmin`). A `role="tablist"` page with five tabs (`[data-testid="admin-tab-<id>-btn"]` / panels `admin-tab-<id>`), each backed by an `/api/admin/*` endpoint ([api.md §1.18](api.md)): **System** (version + git sha, deployment topology, redacted config table, DB stats), **Architecture** (`architecture.md`/`ingestion.md` rendered through the shared `marked` + DOMPurify pipeline), **Coverage** (backend/frontend % vs gate, or an "unavailable" banner), **Users** (table with role/active badges + per-row promote/demote/activate and a create-user form; the current user's self-actions are hidden, and the last-admin 409 surfaces inline), and **Metadata** (reference-taxonomy management, grouped into **Senders**, **Recipients**, **Kinds** and **Currencies** cards, each lazily loaded on first opening the tab). Senders and recipients share the id-keyed **create / rename-with-merge-on-409 / delete-with-reassign** UI; kinds are slug-keyed with a **name-only rename** (a collision is a row error — no merge) and reassign-by-slug delete; the **Currencies** card lists codes-in-use with counts and a **normalise** form (from-select + to-input) behind a confirm step, surfacing the row count and an FX-missing warning (there is no longer a conflict case — the rename writes only `documents`; see [admin.md §1.2.5](admin.md)). After any mutation it reloads the list and refreshes the shared taxonomy cache so other views' dropdowns update; all names render via text interpolation (no `v-html`). See [admin.md §1.2.3](admin.md). |
| `LoginView` | `/login` (`login`, `meta.public`) | **Bypasses the shell** — a centered `w-full max-w-md` Mosaic card on a `bg-gray-100 dark:bg-gray-900` background; `AppInput` + `AppButton` + `AppErrorSummary`. |

### Search modal (`src/components/SearchModal.vue`)

A native `<dialog>` (`showModal()`) mounted once in `DefaultLayout`. Three
entry points, all funnelling into the same instance: the header search button,
the sidebar **Search** nav item (both emit `open-search` →
`searchModal.open()`), and pressing **`/`** anywhere outside a form field.
It exposes `open()` via
`defineExpose`, pre-fills its fields (`AppInput` query, `AppSelect`
kind/sender/tag/**matter**/language fed lazily from the cached taxonomy endpoints,
`AppDateInput` from/to) from the current route query, and on submit pushes the
query to the documents route. Tag and matter are single-select in the modal even
though both are multi-value in the URL: opening pre-fills the select only when
exactly one value is active, and submitting **preserves the original multi-value
set** unless the user picks a specific one (which replaces it) — so a
multi-matter filter set on the dashboard survives editing an unrelated field in
the modal. Native dialog semantics give focus containment,
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
  shared taxonomy cache — `src/composables/taxonomyOptions.ts`, whose
  `useTaxonomyOptions` / `refreshTaxonomyOptions` back every pill's option list —
  via `GET /api/recipients`), Date range, Tag (multi-select —
  `?tag=a&tag=b`), **Project** (multi-select via `AppCheckboxes`, mirroring the
  Tag pill — `?project=a&project=b`, which **OR**-compose, unlike Tag's AND;
  options from the shared taxonomy cache), **Matter** (multi-select via
  `AppCheckboxes` — `[data-testid="pill-matter"]`; the same axis as the
  quick-filter row below, in the standard pill form), and a **More** pill
  covering Language + Status.
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

### Facet filter bar (`src/components/facets/FacetFilterBar.vue`)

One `<select>` per facet, rendered by `DocumentFilterBar` directly below its
business-matter quick-filter row and above its active-filter chips,
following the same mosaic field-row pattern as everywhere else in this bar
(`.filter-label` + `.form-select`, `items-end gap-3` —
`docs/frontend-view-principles.md` §5). The controlled vocabulary itself
(what a facet is, the shipped values, the closed-set rule) is
`docs/facets.md`; this only documents the component.

- **Facets with no values render nothing.** The shipped `vehicle`, `property`
  and `person` facets ship empty (`docs/facets.md` §2), and `FacetFilterBar`
  filters them out (`usable`, `facet.values.length > 0`) rather than showing an
  empty, unusable select — an empty select is worse than an absent one.
- **AND-composition.** Each facet is its own `<select data-facet-select>`
  (`[data-testid="facet-select-<key>"]`); picking a value for one facet and a
  value for another narrows by both, matching the API's `?facet=key:value`
  AND semantics (`docs/api.md` §1.23.4). A facet's own select is single-value,
  so there is no way to pick two values of the same facet from this bar.
- **URL- and saved-view-driven, like every other filter.** `DocumentFilterBar`
  fetches the vocabulary once (`GET /api/facets`, best-effort — the bar just
  renders no selects if that call fails) and carries the *selection* in
  `applied.facets` / `?facet=key:value` (repeatable, one pair per facet) via
  `src/utils/documentQuery.ts`, exactly the mechanism tags/projects/matters
  use — so it survives refresh, back/forward, and "Save view" without any
  facet-specific persistence code.
- **Its own "Clear facets" button** (`[data-testid="facet-clear"]`, shown only
  when a facet is selected) clears every facet selection at once. Facet
  selections do **not** appear as removable chips in the bar's shared
  active-filter-chips row — clearing one facet at a time means reopening that
  facet's select and choosing "Any".

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
note docs, and `preview` only when it would render real content — an image/PDF viewer, a
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
right: `preview` · `markdown`. (`series-chart` was a third right-column card
until 2026-08-31; `DocumentSeriesTrend` went with the series stack. A user whose
`localStorage` still names it is not broken — `reconcileCardColumns` drops any
stored id that is not in `DEFAULT_CARD_COLUMNS` while preserving the order of the
survivors, pinned by `useDocumentLayout.spec.ts`'s "drops series-chart and keeps
every card that still exists".) On drop,
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

### Facet editor (`src/components/facets/FacetEditor.vue`)

The controlled-vocabulary label editor for one document (`docs/facets.md`;
`[data-testid="facet-editor"]`, `#document-facets-card`), rendered as its own
card in the metadata column, after the reorderable metadata tiles.
`DocumentDetailView` loads the vocabulary (`GET /api/facets`, best-effort —
the editor just renders no facets if it fails) and the document's own labels
(`GET /api/documents/{id}/labels`) once and hands both down as props.

- **Renders EVERY facet, including ones with no values yet**, as a disabled
  select (`[data-testid="facet-edit-<key>"]`, `disabled` when
  `facet.values.length === 0`) with a "No values yet" hint underneath. This is
  deliberately the **opposite** of the filter bar above, which omits an empty
  facet entirely: there an empty select would just be noise, but here the
  owner needs to **see** that a facet such as `vehicle` exists before they can
  ask for a value to be added to it.
- **Only changed facets are sent.** A `dirty` computed diffs the in-progress
  draft against the last-saved label map and sends just the facets that
  differ; a facet the user clears is sent as an explicit `null` (never
  omitted) so `PUT /api/documents/{id}/labels` removes that label rather than
  leaving the previous value in place (`docs/api.md` §1.23). The **Save
  labels** button (`[data-testid="facet-save"]`) is disabled while saving or
  while nothing is dirty; a failed save leaves the draft in place (never
  silently discarded) and shows an inline error
  (`[data-testid="facet-error"]`).
- **Not part of the drag-reorder system.** The card sits as a fixed sibling
  in `#document-metadata-column`, after the reorderable `metadataCards` list,
  but it is not one of `useDocumentLayout`'s persisted `cardColumns` — it
  carries no `[data-card-drag-handle]`, so it never gets a drag handle and
  cannot be moved or hidden in **Edit layout** mode the way the section tiles
  above it can. This is a **known limitation**, not an intentional design
  choice — a facet-labels card is exactly the kind of section a user would
  reasonably expect to reorder alongside the others.

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
in use with counts and offers a **normalise** form (from-select +
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

### Spending board and workspace (`components/spending/`)

The board and workspace views above are built from a dedicated component set
under `src/components/spending/`, plus two pure modules under `src/spending/`
that own money arithmetic and band/colour derivation so no component computes
either itself:

- **`spending/money.ts`** — exact arithmetic over the decimal-string amounts
  the spending API sends (`toCents`/`fromCents`/`formatMoney`). Money crosses
  the wire as a string (`"1284.50"`), never a JSON number: subtracting two
  amounts as floats loses cents (`1284.50 - 1142.20` is
  `142.29999999999998` in IEEE754), so every money computation on this
  surface — ranking split values for the fold, the headline's
  period-over-period delta — is done in integer cents and formatted back.
- **`composables/facetVocabulary.ts`** — the controlled facet vocabulary,
  fetched once and shared, as a Pinia store behind a `useFacetVocabulary()`
  wrapper. Modelled on `composables/taxonomyOptions.ts` deliberately (same
  `ensureLoaded()` promise latch, same best-effort failure) — a second caching
  idiom would be a worse outcome than a second fetch. The reason it exists is
  not the app-wide request count but that two consumers appear on **one
  screen**: the workspace's drill panel needs the vocabulary for its label
  editor and `ChartRuleEditor` needs it for its clause rows, and two independent
  snapshots of a mutable list can disagree about which values still exist —
  which is exactly the state the editor is built to show and repair. Adopted in
  those two; the four unrelated `fetchFacets` call sites keep their local refs.
  Because it is a Pinia store and there is no global `setupFiles`, every spec
  that mounts a consumer needs `setActivePinia(createPinia())`.
- **`spending/ruleText.ts`** — `ruleSummary()` / `splitSummary()`, a rule
  rendered as one line of English. Shared because there are two renderers of the
  same rule — `QuestionDraft`'s summary line and `ChartRuleEditor`'s — and two
  copies would drift into visibly disagreeing about one rule. Its `facets`
  argument defaults to none, in which case it prints raw facet and value
  **keys**: that is what `QuestionDraft` does, since it holds no vocabulary and
  adding a fetch there for labels alone would not pay. A key the vocabulary
  cannot resolve falls back to the key rather than disappearing — the same rule
  the editor's chips follow, defined once here.
- **`spending/palette.ts`** — `bands()`, the one place that turns a chart's
  raw splits and cells into the ordered, coloured stack bands every chart
  component draws: sums cell totals per split value in exact cents, folds
  whatever doesn't fit the shared six-slot palette
  (`@/utils/splitPalette` — see below) into a single "Other" band without
  losing a cent, fixes a deterministic band order so a chart never repaints
  on refetch, and assigns each band a colour (a stored colour wins its slot
  first; everything else is hash-derived and de-collided against what's
  already claimed). Every consumer — `SpendingChart`, `SpendingLegend`,
  `SpendingCard`, the drill panel — reads `bands()`'s output; none derives a
  colour of its own.
- **`SpendingChart.vue`** — the stacked-bar mark (Chart.js via
  `vue-chartjs`). Draws `band.light`/`band.dark` verbatim; the one exception
  is the unsplit chart (`bands` is `[]`), which draws its single series in
  `SPLIT_PALETTE[0]`. Never keyed on `data`, so a parent can hold the
  previous render on screen (dimmed) through a refetch without a flash.
- **`SpendingLegend.vue`** — a display filter over the same `bands` prop the
  chart draws from. Click **isolates** a band; modifier-click (Cmd/Ctrl)
  **excludes** just that one. Both are client-side filters the parent also
  applies to the chart — this component never removes a hidden row from its
  own list, only marks it, so there is always a way back to "Show all". Each
  row's `aria-pressed` is repurposed to mean "this band is visible", not the
  conventional pressed/active toggle reading — deliberate, documented at the
  call site.
- **`SpendingFooter.vue`** — the "nothing is excluded silently" accounting
  statement: every document a chart's rule touched but its total did not
  count, in three labelled blocks. A refund is **netted**, not excluded — it
  stays inside the header total and lowers it, and must never appear here.
- **`SpendingCard.vue`** — one saved chart as it appears on the board: name
  (a link to `/charts/{id}`, the only route from the board into the
  workspace) + overflow menu, headline figure (the most recent *complete*
  bucket, never the last one drawn — the current period is always partial),
  compact chart, legend ribbon, a needs-attention line. Fetches nothing about
  the chart's *data* — `data` / `error` / `busy` are handed down by
  `SpendingBoardView`, which is what keeps one card's failure from taking
  down the rest of the grid. The overflow menu is **Move up / Move down /
  Rename / Delete**; Rename and Delete are each two-step in place (an inline
  name input with Save/Cancel; a Confirm/Cancel pair) rather than firing on
  one click — Rename issues its own `PATCH /api/spending/{id}` (the one
  action this card fetches for itself) and emits `renamed`; Delete still
  emits `delete` for the board to act on, only gated behind the confirm step.
  It stays a list of **four**: editing a chart's rule is reached by opening the
  chart, not from this menu (`SpendingWorkspaceView`, above), so a fifth entry
  added here for symmetry would be wrong.
- **`SpendingDrillPanel.vue`** and its three bodies — the drill-through
  shell (a native `<dialog>` opened with `showModal()`, side-panel or
  bottom-sheet presentation) plus `DrillCellBody` (one bar: period × split
  value), `DrillBucketBody` (a footer exclusion bucket, paginated past the
  server's page cap), and `DrillOtherBody` (the folded "Other" band's
  second step — reads cells `/data` already fetched for the clicked period
  and issues no request of its own, which is the entire reason the fold
  "costs no request"). Each body owns its own fetch; the shell fetches
  nothing and knows nothing about drill content.
- **`QuestionDraft.vue`** — the board header's "ask a question" free-text
  flow (`POST /api/spending/draft`), rendering exactly one of three states:
  expressible (preview, save enabled), partly-expressible (the same preview,
  labelled an approximation, plus `unknown_terms`), or collapsed
  (`unknown_terms` and a message only, **no preview**, save disabled — a
  fully-dropped rule would preview as "every document in the archive",
  which is the most confidently wrong answer this feature can give).
- **`ChartRuleEditor.vue`** — the workspace's rule editor: a saved chart's rule
  as editable rows (facet / `is`–`is not` / values), plus the split-axis picker,
  previewing through `POST /api/spending/preview` and saving through
  `PATCH /api/spending/{id}`. It owns its own write, the way `SpendingCard` owns
  its rename, because a failed save must leave the edited rows on screen and
  only the component holding them can promise that.

  The invariant it is built around: rows are seeded from `chart.rule.all`
  **verbatim**, and the vocabulary only *labels* and *offers* — it never
  filters. A chart naming a value deleted since it was saved still loads, and
  this editor is what repairs it, so a lost value renders as a checked, flagged,
  removable chip (`AppCheckboxes` reports it checked because it is in the model,
  and its `hint` carries the explanation) and a lost facet as a disabled
  placeholder option. Filtering the rows against the live vocabulary is a
  one-line change that would silently delete the clause the owner came to fix.

  The values column is `AppCheckboxes` inside `FilterPill`, the
  `DocumentFilterBar` idiom. **`AppMultiSelect` was rejected**: it offers to
  *create* a typed value, and the facet vocabulary is closed — the API 422s on a
  value that does not exist — so its primary affordance would be a guaranteed
  error. The popover also keeps the values list out of flow, which is what lets
  a three-control row survive the 343px content column at a 375px viewport; the
  row itself stacks below `@lg/workspace` and the geometry is asserted in
  Playwright, never jsdom (§1.7.3).
- **`SpendingEmptyState.vue`** — the board's zero-charts screen. "All
  spending" is pinned first, created through the ordinary `POST
  /api/spending` path (not a migration seed — see §2.2 of the design spec
  for why a seeded row is wrong: a display currency nobody chose, and a
  one-shot that means "gone once deleted"). Its split axis degrades to
  whatever vocabulary actually exists: `default_split: 'category'` only
  works once the `category` facet has been seeded (`library
  label-archive` — an operator step, never automatic on migrate/startup), so
  a genuinely fresh archive proposes the chart **unsplit** instead, keyed on
  whether `GET /api/facets/counts` returned anything at all rather than on
  the literal `category` (a probe for that one facet by name would stay
  broken for an archive whose vocabulary exists but happens to have no
  `category` values yet). Every other proposal comes from that same counts
  route, ranked by document count.

`@/utils/splitPalette` (`SPLIT_PALETTE`, `deriveSlot`, `resolveSplitColour`) is
a **shared** six-slot palette module, not owned by this feature — a parallel
plan's split-vocabulary picker uses the same colours, so there is exactly one
mapping from a split value to a slot rather than two that could disagree.

#### Two container-query thresholds, not one

`PageHeader`'s `#controls` merge (§1.3, `@5xl`) already gates the header
toolbar on a **container** query rather than a viewport one, because the
content column is the viewport minus a sidebar the user collapses
independently. The workspace repeats that pattern at a second, smaller
threshold (`@3xl`, 48rem = 768px, spec §4.13) for its own toolbar and drill
panel, and adds two things the header toolbar never needed. Measured directly
in the real stack via Playwright (Task 10/12), not read off a class list:

| viewport | sidebar | `#app-page` border box | padding | content box |
| --- | --- | --- | --- | --- |
| 1280 | expanded | 1024 | 32 | **960** |
| 1280 | collapsed | 1200 | 32 | **1136** |
| 656 | overlay | 656 | 24 | **608** |
| 375 | overlay | 375 | 16 | **343** |

Three facts worth stating explicitly, each of which cost something to learn:

1. **A container query on `inline-size` evaluates against the content box**,
   so the padding comes off. Reading the border box predicts the wrong side
   of the threshold for the 1280-expanded case at the header's own `@5xl`
   (1024px = 64rem) boundary: the border box (`#app-page`) is **exactly**
   1024px, which reads as "at or above the threshold, should merge" — but
   the content box the query actually evaluates is 960px, **below** it, and
   the header correctly stacks. Reading the border box would have predicted
   the opposite outcome from the one the browser produces. This is why the
   table records the content box explicitly rather than leaving it to be
   inferred from the border box.
2. **The workspace uses a named container** (`@container/workspace` on the
   view root, `@3xl/workspace:` on its classes), not an unnamed one, because
   `PageHeader` already opens its own **unnamed** `@container` around the
   `#controls` slot. An unnamed `@3xl:` inside the workspace would silently
   bind to PageHeader's container instead of the workspace root — caught
   before shipping (Task 10), not after.
3. **The drill panel cannot use a container query at all.** A `<dialog>`
   opened with `showModal()` is in the browser's **top layer**, so it is not
   a descendant of any container in the document — no `@container` rule
   reaches it and no custom property inherits into it. `SpendingWorkspaceView`
   instead runs a `ResizeObserver` on its own content column
   (`SHEET_THRESHOLD_PX`, the same 768px constant the `@3xl/workspace` class
   is keyed to) and hands the panel a resolved `sheet` boolean as a prop.
   Never the viewport — `matchMedia`, `innerWidth`, and a `lg:` class are all
   wrong here for the same reason as fact 1: none of them can distinguish a
   960px column from a 1136px one at the same 1280px viewport width.

Both guards were watched failing before being trusted, not merely written and
assumed correct. Swapping the workspace's `@3xl:` for `lg:` and running
`e2e/spending-layout.spec.ts` reds at a 1024px viewport with the sidebar
expanded — "column is 704px (<768px) — the chip must show" — where the chip
resolves to hidden instead, because `lg:` (1024px) reports the viewport is
wide enough while the 704px content column is not. The pre-existing `@5xl` →
`lg:` swap on `e2e/header-toolbar.spec.ts` reds the same way at the header's
own threshold: at 1280px with the sidebar expanded the column is too narrow to
merge, expected `false` (stacked) received `true` (merged).

## 1.6 Dark mode

Dark mode is class-based (`.dark` on `<html>`, driven by `ThemeToggle` /
`useDark`) and surfaced through the `dark` custom variant defined in `main.css`.
Every shell component, `App*` component, and view carries `dark:` variants, so
the whole app — backgrounds, cards, borders, text, form controls — responds to
the toggle. The user's choice is persisted by `useDark` (localStorage).

## 1.6.1 PWA wiring

The app ships an installable web-app manifest — `frontend/public/manifest.webmanifest`
(`display: minimal-ui`, `scope`/`start_url` `/`, theme and background `#f3f4f6`)
plus three icons under `frontend/public/icons/` (192, 512, and a 512 maskable).
`frontend/index.html` links the manifest, the `apple-touch-icon`, both favicons,
and sets `theme-color`.

There is no service worker and no offline mode: installability is the whole
feature — Library is a network-backed archive and an offline shell would be a
lie. Lighthouse's installability audit needs a served origin and a headed
Chrome, so it is deliberately not in CI; `frontend/src/__tests__/pwa.spec.ts`
covers the regression that actually bites instead — the manifest stays linked,
parseable and complete, and every icon it references really ships.

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
- `npm run test:e2e` — Playwright against the real stack. Five projects: desktop Chromium, mobile WebKit (375 px, iPhone 14), tablet WebKit (iPad gen 11), **desktop Firefox**, and **desktop WebKit** (Safari). The chromium/mobile/tablet projects run the full suite; the two desktop-engine projects are **scoped (via `testMatch`) to `e2e/pdf-preview.spec.ts` only** — they exist to prove the self-rendered PDF preview behaves identically across all three engines, without forcing the rest of the suite onto Firefox. That spec proves canvases paint and scrolling reveals page 2 on each engine. Recent flows have their own specs in `frontend/e2e/`: **`markdown-reader`** (upload a `.md` → the reader renders), **`projects`** (create → assign via the token multiselect → see it on the `/projects` index → filter the dashboard by it), **`notes`** (author a note → edit in place → restore a version), **`topics-readonly`** (topics show as read-only badges with no editor), **`admin-views`** (a normal user sees no Admin link and is redirected from `/admin`; an admin reaches `/admin` and all five tabs render), **`tile-border-colour`** (sets a per-kind override and asserts the tile's *computed* border colour — the cascade-layer regression guard), **`review-queue`** (a future-date edit flags a doc `needs_review`, then the queue is entered, advanced, and exited), and **`held-emails`** (navigation + empty state only — the e2e stack has no IMAP, so the hold flows live in vitest + backend tests). The admin spec needs a second admin login (`E2E_ADMIN_USERNAME`/`E2E_ADMIN_PASSWORD`; CI creates an `e2e-admin --admin` user). Every spec is gated by `requireStack()` (`e2e/fixtures/require-stack.ts`), which **skips locally and throws in CI** — see §1.7.1. (CI installs all three engines — `chromium firefox webkit` — in `.github/workflows/ci.yml`.)

### 1.7.0 The nightly retrieval-recall heartbeat

One measurement does not run in the PR gate and cannot: `library eval-recall`
needs the **embedder** to produce real vectors, and the `e2e` job starts
`db migrate api worker` only — no embedder at all, so recall cannot pass there
by construction. TEI publishes no arm64 image either, so it cannot be run on an
Apple Silicon development machine; only a host with a reachable embedder can
drive it.

`.github/workflows/e2e-nightly.yml` runs it nightly (03:20 UTC, plus
`workflow_dispatch`) with the whole stack including the embedder, waiting on
TEI's own `/health` from inside the compose network — polled through the `api`
container, because the `embedder` service publishes no ports and is reachable
only as `http://embedder:80` — before measuring. A started container is not a
warm embedder, which is why it waits on `/health` and not on `up -d`.

The workflow's one job is `retrieval-recall`. It used to be `smart-groups`, and
carried a browser journey (`smart-groups.spec.ts`) as well; that spec, the
`E2E_SMART_GROUPS` flag and the `assert-e2e-ran.mjs` did-it-actually-run
assertion all went with the series stack on 2026-08-31. The recall step needs
none of them: it is a CLI command run inside the `api` container that seeds and
queries the database directly, with no authenticated user and no HTTP call
through the app, so there is no "silently skipped" failure mode for that
assertion to catch.

The measurement step carries `continue-on-error: true` and only ever **reports**.
It cannot gate today: the recall corpus is deliberately built so some cases fail
at baseline (`library.ask.recall_scenarios`), so a gate here would fire on the
corpus's own design. Gating on a *regression* becomes possible once every case
passes at baseline and stays there — a deliberate follow-up. The workflow is also
**not** in `ci-gate` and not a `promote` gate: a nightly failure is a signal to
look, not a merge blocker.

### 1.7.1 The e2e job cannot pass without running

`playwright test` **exits 0 when every test skips**, and there is no
`--fail-on-skip`. Every spec used to open with
`test.skip(!BASE_URL, 'E2E_BASE_URL is not set …')`, so deleting `E2E_BASE_URL`
from the workflow made all 19 specs skip and the job report **green having
launched nothing**. Nothing distinguished "the stack was fine and everything
passed" from "the stack was never there".

Two independent guards now, because they fail in different ways:

1. **`requireStack()`** — `throw` when `CI` is set and `E2E_BASE_URL` is not;
   `test.skip` otherwise. The local behaviour is deliberately unchanged: running
   `npm run test:e2e` without a stack still skips cleanly, which is the whole
   reason the original gate existed.
2. **`scripts/assert-e2e-ran.mjs`** — parses the Playwright JSON report and fails
   when fewer than 40 tests executed, or when any skip reason mentions
   `E2E_BASE_URL`. Not redundant: a reporter change, a `--grep` that matches
   nothing, a project-filter typo, or a spec that fails to collect all produce a
   green run with too few tests and none of them trip a throw at module scope.
   A missing or unparseable report exits 2 — distinct from 1, because "no report"
   and "nothing wrong" must not share an exit code.

CI runs **`npm run test:e2e:ci`**, which chains the assertion; local
`npm run test:e2e` is untouched. The floor of 40 is set well below a real run's
count so adding or removing a test does not red it — it answers "did the suite
run at all", not "how many tests are there".

Demonstrated rather than assumed: with `CI=1` and no `E2E_BASE_URL` the run exits
**1**; without `CI` it reports **32 skipped** and exits 0.

### 1.7.2 Accessibility lint

`eslint-plugin-vuejs-accessibility` (`flat/recommended`) runs as part of
`npm run lint`, which CI already gates. The app had 172 `aria-*` attributes, 54
roles and a native `<dialog>` with focus restore, and **nothing protected any of
it**.

Vue's own `flat/essential` is deliberately **kept** rather than upgraded to
`flat/recommended`, which the plan proposed as "near-zero cost". Measured, it is
not: `flat/recommended` adds **1,598 pure-formatting violations**
(`vue/html-indent`, `vue/max-attributes-per-line`, …) against **55** real a11y
ones. That is an enormous diff for no reader benefit, and it would bury the
signal the a11y plugin exists to surface. The formatting upgrade is a separate
decision.

Two real defects were fixed:

- **`ThreadActionsMenu.vue`** announced `role="menu"` with `role="menuitem"`
  children and implemented none of the ARIA menu keyboard contract — no arrow
  keys, no roving tabindex, no focus-in on open. The roles are removed, so it now
  announces as what it is: two ordinary buttons in a container, Tab-reachable and
  Enter/Space-operable. (`AppPopover.vue` implements the contract properly and is
  the model if the roles are ever wanted back.)
- **`AppSidebar.vue`** made the persistent wordmark an `<h1>`, so every
  authenticated page had two competing top-level headings alongside the page
  title's. It is a `<p>` now, with identical classes — a screen-reader heading
  list no longer leads with "LIBRARY" instead of the page title. (That page
  `<h1>` has since moved from `PageHeader` into `AppHeader`; it is still exactly
  one per page, and `AskView`'s mobile list screen gave up its own duplicate
  "Ask" heading when it did.)

The remaining rules are **off with a named exit** in `eslint.config.ts`, matching
the mypy ratchet's shape: the gate is real from its first run for everything
else, and new code cannot regress the rules that are on. One is off permanently
rather than as a ratchet, and the reason is worth knowing before someone "fixes"
it: **`no-redundant-roles` fires on `role="list"` on a `<ul>`, and that role is
load-bearing here** — Tailwind preflight sets `list-style: none`, and Safari drops
list semantics from an unmarkered list, so removing the role to satisfy the
linter would silently cost VoiceOver users the list. The genuinely redundant case
(`<fieldset role="group">`) was fixed instead.

### 1.7.3 Layout is asserted in Playwright, never in jsdom

**The rule: layout is asserted against real rects in Playwright. jsdom specs
assert behaviour and data flow only.**

It is written down because breaking it is so easy and so quiet. jsdom has no
layout engine, so `getBoundingClientRect` returns zeros — which means a
component spec can only test layout by *mocking* the rect, and a mocked rect
asserts the mock. Three separate composer fixes (`bf8da0c`, `60a2f06`,
`5a878a0`) each shipped with unit specs that asserted class strings, and none of
them could have caught the next regression.

The layout specs are `responsive.spec.ts`, `ask-layout.spec.ts`,
`detail-layout.spec.ts`, `charts-layout.spec.ts` and `header-toolbar.spec.ts`,
sharing
`e2e/fixtures/layout.ts` (overflow, grid column count, rect reads, docking and
overlap checks, and the internal-scroll helper). Two facts about this app that
those helpers encode, because both cost real time to rediscover:

- **`window.scrollTo` does nothing.** The shell is a fixed-height flex column
  and `#app-content` is the element that scrolls, so the document's own
  `scrollHeight` always equals its `clientHeight`. Use
  `scrollAppContentToBottom`.
- **Some things are not in the DOM until you scroll.** The detail view's action
  dock is `v-if`-mounted by an IntersectionObserver on the hero, so a spec must
  scroll it into existence — and the shared fixture document is exactly one
  viewport tall, so `detail-layout.spec.ts` seeds its own long note first.

The two `App*` specs that mock rects (`AppPopover.spec.ts`, `FilterPill.spec.ts`)
are deliberately left alone: they test open/close behaviour, not geometry.

## 1.8 What did not change

The reskin touched only the presentation layer. Unchanged **as of the reskin**
(2026-06-13) — this is a historical statement, and both directories have grown
since, so read it as "the reskin did not touch these", not as a current
inventory:

- `src/api/` — `client.ts` (fetch + CSRF double-submit), `documents.ts`,
  `taxonomy.ts`, `settings.ts`. API contracts per [api.md](api.md).
  (Since added: `admin`, `ask`, `heldEmails`, `matters`, `notes`, `projects`,
  `savedViews`.)
- `src/stores/` — `auth.ts` (`useAuthStore`: `user`, `isAuthenticated`,
  `ensureLoaded()`, `login`/`logout`, `dashboardFields`, `applyPreferences`)
  and `flash.ts`. (Since added: `heldEmails`, `jobs`, `notifications`,
  `reviewQueue`, `savedViews`; and `auth` has grown `isAdmin`, `backgroundTone`,
  `tilePreview`, `dockPosition`, `phoneColumns`, `hideSummaryMobile`,
  `notificationSettings`, `kindColors`, `askProfile`.)
- `src/router/index.ts` route table and `authGuard` logic (only the rendered
  shell around the routes changed).
- The backend, auth/session/CSRF behaviour, and the snippet/highlight XSS-safety
  helpers in `src/utils/`.
