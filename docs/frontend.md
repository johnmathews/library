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

## 1.4 Screens

Typed API layer: `src/api/documents.ts` mirrors the backend schemas
(`DocumentListItem`, `DocumentListResponse`, `DocumentDetail`,
`DocumentFilters` including repeatable `tag`, `UploadResult`, `JobInfo`)
and wraps `GET/POST /api/documents`, `GET /api/documents/{id}` and
`GET /api/jobs`. Uploads go through `XMLHttpRequest` (fetch has no upload
progress events); 200/201 resolve, 409/413/415/network reject with
`ApiError`. The 11 seeded kinds are duplicated as `DOCUMENT_KINDS`
because **there is no taxonomy list endpoint yet** — senders and tags
therefore have no filter UI (see Known gaps below).

### 1.4.1 Documents list — `/` (`DocumentListView`)

GOV.UK-style search results: per row a thumbnail
(`/api/documents/{id}/thumbnail`, with a file-type placeholder when
`has_thumbnail` is false or the image 404s), title linking to
`/documents/:id`, kind and language tags, sender, document date, and —
when searching — the `ts_headline` snippet. Search box (`q`, websearch
syntax) plus a collapsible filter panel (kind, language, date range using
`GovDateInput`); `GovPagination` drives `limit`/`offset` (25 per page).

All applied state lives in the **URL query**
(`?q=…&kind=…&language=…&date_from=…&date_to=…&page=…`), so back/forward
and refresh restore both the form and the results. Two distinct empty
states: an empty library (inset text linking to `/upload`) vs. a search
with no matches (inset text offering to clear filters).

### 1.4.2 Document detail stub — `/documents/:id` (`DocumentDetailView`)

Minimal W10 placeholder: title, a four-row summary list (status, kind,
sender, date) and a back link. W11 replaces it with preview, metadata
editing, downloads and delete.

### 1.4.3 Upload — `/upload` (`UploadView`)

`GovFileUpload` (govuk-frontend v6.2 enhanced drop-zone on desktop) with
`multiple` and `accept="image/*,application/pdf"`. There is deliberately
**no `capture` attribute**: with `accept` alone, iOS/Android offer both
the camera and the photo library; `capture` would force the camera and
hide the library.

Each selected file uploads independently: XHR progress feeds an
`AppProgressBar`, then the document is polled (`GET /api/documents/{id}`,
2s interval, 3min cap) until the pipeline reaches `indexed` or `failed`.
Outcomes: success banner with a link to the document; duplicate (200 with
`duplicate: true`) gets a notification banner — "already in your library"
— linking to the existing document; 413/415/network failures land in a
GOV.UK error summary. One file failing never blocks the others.

**Progress bar extension.** GOV.UK has no progress component;
`src/components/AppProgressBar.vue` is an app extension styled with
design-system colours/spacing only (`govuk-colour("blue")` fill, black
border, tabular-numbers percentage) and exposes `role="progressbar"` with
`aria-valuenow`/`aria-label`.

### 1.4.4 Known gaps

- No `GET /api/senders` / `GET /api/tags` (or kinds) list endpoints exist,
  so the list view cannot offer sender/tag filter options and the kind
  options are a hardcoded copy of the migration's seed data. When taxonomy
  endpoints land, replace `DOCUMENT_KINDS` and add the two filters.

## 1.5 Snippet safety

`GET /api/documents?q=…` returns `snippet`: `ts_headline` fragments over
**raw OCR text** with `<b>`/`</b>` highlight markers. The server does NOT
HTML-escape it — a scanned document can contain literal HTML
(docs/api.md §1.3.3). The frontend contract, implemented in
`src/utils/snippet.ts`:

1. `renderSnippet()` escapes *all* HTML-special characters, then
2. converts only the exact sequences `&lt;b&gt;` / `&lt;/b&gt;` back into
   real `<b>` / `</b>` elements.

The result is the **only** string ever bound with `v-html` (one
annotated site in `DocumentListView`). `src/utils/__tests__/snippet.spec.ts`
proves script tags, event-handler attributes and attribute-smuggling
through the markers are all neutralised. Never bind a raw snippet —
add new render sites only via `renderSnippet`.

## 1.6 End-to-end tests (Playwright)

`frontend/e2e/library.spec.ts` runs the W10 acceptance against the
**real stack**: sign in → upload `e2e/fixtures/library-fixture.pdf` (a
checked-in one-page PDF whose text layer contains Dutch words, including
"rekeningen") → wait for `Indexed` (or the duplicate banner on re-runs)
→ see it in the list → search the stem `rekening` and assert a
highlighted snippet. Claude extraction is not required: assertions rely
only on the OCR/text-layer pipeline, never on extracted metadata, so the
suite passes without an Anthropic API key.

Two projects only (W16 widens the matrix): `chromium` (desktop) and
`mobile-webkit` (iPhone 14 device descriptor pinned to the 375px
acceptance viewport). They run serially against one backend; the second
project re-uploads the same bytes and exercises the duplicate path.

The suite **skips itself** when `E2E_BASE_URL` is unset, so
`npx playwright test` is a no-op without the stack and `npm run
test:unit` stays pure (vitest excludes `e2e/**`). To run locally, from
the repo root:

```console
docker compose -f docker-compose.yml -f frontend/e2e/compose.e2e.yml \
  up -d --build db migrate api worker
echo 'e2e-password-123' | docker compose exec -T api \
  library user add e2e --display-name "E2E" --password-stdin   # once
cd frontend && npm run build-only
npm run preview -- --port 4173 --strictPort &   # proxies /api → :8000
E2E_BASE_URL=http://localhost:4173 npm run test:e2e
```

The compose override sets `LIBRARY_COOKIE_SECURE=false` — the stack is
reached over plain HTTP, so a `Secure` session cookie would be dropped by
the browser. `vite preview` reuses `server.proxy` from `vite.config.ts`,
so `/api` reaches the compose API on `:8000`. CI runs the same recipe in
the `e2e` job of `.github/workflows/ci.yml` (user `e2e`, password
`e2e-password-123`, overridable via `E2E_USERNAME`/`E2E_PASSWORD`).

## 1.7 Tests and checks

- `npm run test:unit -- --run` — component markup/behaviour specs
  (error-summary focus, conditional reveals, date-input ISO emission,
  FileUpload init), API client CSRF behaviour, documents API (query
  serialisation, XHR upload incl. progress/duplicate/415/network), snippet
  XSS contract, auth store, router guard, login view flows, document list
  (rows, empty states, URL-synced filters, pagination, snippet rendering)
  and upload view (progress, polling, duplicate banner, error summary,
  multi-file independence).
- `npm run lint`, `npm run type-check`.
- `npm run build && npm run check:assets` — licensing gate (§1.2.1).
- `npm run test:e2e` — Playwright against the real stack (§1.6).
