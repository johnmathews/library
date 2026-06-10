# Frontend

The Library web UI: a Vue single-page app styled with the GOV.UK Design
System, minus the parts of GOV.UK that are legally restricted to gov.uk
services.

## 1.1 Stack

| Layer | Choice |
|-------|--------|
| Framework | Vue 3.5 (Composition API, `<script setup>`, TypeScript) |
| Build | Vite 8 (Rolldown), `sass-embedded` for SCSS |
| Routing | vue-router 5 (history mode) |
| State | Pinia 3 |
| Design system | govuk-frontend 6.2 (SCSS + ES-module JS, MIT licence) |
| Typeface | Inter, self-hosted via `@fontsource/inter` |
| Tests | Vitest 4 + @vue/test-utils, jsdom |

Everything lives in `frontend/`. `npm run dev` proxies `/api` and
`/healthz` to the backend on `localhost:8000` (see `vite.config.ts`).

## 1.2 Design-system approach

We consume govuk-frontend's **code** (MIT) and replace its **restricted
assets**:

- **What we copy:** the compiled design system — SCSS settings/helpers/
  components, the responsive type scale, GOV.UK breakpoints (320/641/769/
  1280), focus states, form patterns, and the documented component markup.
  Our Vue wrappers emit the exact HTML from govuk-frontend's own rendered
  fixtures (`node_modules/govuk-frontend/dist/govuk/components/*/template-*.html`).
- **What we must not ship:** the GDS Transport typeface and crown/crest
  imagery are licence-restricted to services on gov.uk. They are never
  bundled, served, or referenced.

### 1.2.1 Font and crown substitution

- `src/styles/main.scss` configures the Sass module once:
  `$govuk-font-family: ("Inter", arial, sans-serif)` and
  `$govuk-include-default-font-face: false` (belt-and-braces: in v6 this
  flag already defaults to false when "GDS Transport" is absent from the
  font stack).
- Inter 400/700 in `latin` + `latin-ext` subsets (Dutch diacritics) is
  imported in `src/main.ts` from `@fontsource/inter` and bundled by Vite —
  no CDN, no Google Fonts.
- Components are imported **individually** rather than via the all-in-one
  `govuk-frontend/dist/govuk` index, because the footer component's CSS
  references `govuk-crest.svg` (royal crest). The header (crown logotype)
  and footer are replaced by an `app-masthead` (text-only "Library" bar)
  and `app-footer`, styled in `main.scss` with govuk Sass helpers.
- `frontend/scripts/check-assets.mjs` (`npm run check:assets`) scans
  `dist/` after a build and fails on: file names matching
  `/transport|crown|crest|gds/i`, web fonts that are not `inter-*`, or
  text content matching GDS Transport / crest / GOV.UK asset-URL patterns.
  CI runs it after `npm run build`.

### 1.2.2 Page template

`index.html` carries the GOV.UK template classes (`govuk-template` on
`<html>`, `govuk-template__body` on `<body>`) plus the standard inline
script that adds `js-enabled govuk-frontend-supported` — govuk-frontend
JS components refuse to initialise without that marker. `App.vue`
provides the shell: skip link, masthead, `GovServiceNavigation`, a beta
phase banner, `govuk-width-container` / `govuk-main-wrapper`
(`#main-content`), and the minimal footer.

### 1.2.3 Component inventory

Thin SFC wrappers in `src/components/govuk/` (barrel: `index.ts`).
Props loosely follow the GOV.UK nunjucks macro options (`label`, `hint`,
`errorMessage`, `id`/`name`); form components use `defineModel()`.

| Wrapper | govuk-frontend JS init? | Notes |
|---------|------------------------|-------|
| `GovButton` | yes (`Button`) | `variant` prop; `href` renders `<a role="button">` |
| `GovInput` | no | label/hint/error wiring, `aria-describedby` |
| `GovTextarea` | no | |
| `GovSelect` | no | `items: SelectItem[]` |
| `GovRadios` | **deliberately no** | conditional reveal driven by Vue (slot `conditional-<value>`); initialising govuk Radios JS would fight Vue for the same DOM |
| `GovCheckboxes` | **deliberately no** | as GovRadios; `string[]` model |
| `GovErrorSummary` | yes (`ErrorSummary`) | focuses itself on mount, links move focus to fields; re-focuses when the error list changes |
| `GovErrorMessage` | no | standalone error paragraph |
| `GovSummaryList` | no | rows + optional action links |
| `GovTag` | no | `colour` modifier |
| `GovPagination` | no | emits `change(page)`; GOV.UK condensed page list |
| `GovNotificationBanner` | yes (`NotificationBanner`) | `variant="success"` renders `role="alert"` and is focused on mount |
| `GovServiceNavigation` | yes (`ServiceNavigation`) | mobile menu toggle; RouterLink items + action items (emit `select`) |
| `GovFileUpload` | yes (`FileUpload`) | v6.2 enhanced drop-zone; v-model is `File[] \| null` |
| `GovPanel` | no | confirmation panel |
| `GovDetails` | no | native `<details>` |
| `GovBackLink` | no | RouterLink or `<a>` |
| `GovDateInput` | no | 3-field GOV.UK date pattern; v-model is an ISO `YYYY-MM-DD` string or `null` |

JS-backed components use `useGovukComponent(rootRef, ComponentClass)`:
instantiates in `onMounted` guarded by `isSupported()`, clears the
`data-<module>-init` marker on unmount (v6 components have no
`destroy()`; listeners die with the element).

### 1.2.4 Adding a component

1. Find the rendered markup in
   `node_modules/govuk-frontend/dist/govuk/components/<name>/template-*.html`
   (do not guess class names) and translate it into an SFC under
   `src/components/govuk/`.
2. Add the component's SCSS to the `@use` list in `src/styles/main.scss`.
   Check its `_index.scss`/`_mixin.scss` for `govuk-image-url`/
   `govuk-font-url` references first — anything crown/crest/Transport
   related stays out.
3. If `dist/govuk/all.mjs` exports a class for it, wire
   `useGovukComponent` and extend `src/types/govuk-frontend.d.ts`
   (the package ships no TypeScript declarations).
4. Export from `index.ts`, add a Vitest spec asserting the load-bearing
   classes/ARIA, and run `npm run build && npm run check:assets`.

## 1.3 Auth integration

Contract: [api.md](api.md) §1.9.

- `src/api/client.ts` — `apiFetch<T>(path, options)`: fetch with
  `credentials: 'same-origin'`, JSON in/out, query-string helper. For
  POST/PUT/PATCH/DELETE it reads the `library_csrftoken` cookie and sends
  it as `X-CSRF-Token` (double-submit; the `library_session` cookie is
  httpOnly and travels automatically). Non-2xx responses throw `ApiError
  { status, detail }` with the backend's `detail` normalised to a string.
- `src/stores/auth.ts` — `useAuthStore`: `user`, `isAuthenticated`,
  `ensureLoaded()` (calls `GET /api/auth/me` once and caches; 401 →
  `null`), `login()`, `logout()`.
- Router guard (`authGuard` in `src/router/index.ts`): non-public routes
  require a user; otherwise redirect to `/login?redirect=<fullPath>`.
  After login the view returns to the original target. Signed-in users
  visiting `/login` are bounced to the documents page.
- `/login` (`LoginView.vue`) follows the GOV.UK error-summary pattern:
  client-side "Enter your username/password" errors and the API's generic
  401 both render a `GovErrorSummary` that takes focus and links to the
  fields.

## 1.4 Tests and checks

- `npm run test:unit -- --run` — component markup/behaviour specs
  (error-summary focus, conditional reveals, date-input ISO emission,
  FileUpload init), API client CSRF behaviour, auth store, router guard,
  login view flows.
- `npm run lint`, `npm run type-check`.
- `npm run build && npm run check:assets` — licensing gate (§1.2.1).
- Playwright smoke tests arrive with the W10 flows.
