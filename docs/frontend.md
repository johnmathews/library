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

Everything lives in `frontend/`. Two serving modes:

- **Dev:** `npm run dev` — Vite on `:5173`, proxying `/api` and
  `/healthz` to the backend on `localhost:8000` (see `vite.config.ts`).
- **Production:** the Docker image builds the SPA (`node:22-slim` stage)
  and the FastAPI process serves `frontend/dist` itself — hashed
  `/assets` immutable, everything else falling back to `index.html` —
  see [deployment.md](deployment.md) §1.3. No separate web server.

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

`GovInput` also takes a `list` prop (the id of a `<datalist>`) for
native autocomplete suggestions — used by the detail page's sender
editor.

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
`DocumentFilters` including repeatable `tag`, `DocumentUpdate`,
`UploadResult`, `JobInfo`) and wraps `GET/POST /api/documents`,
`GET/PATCH/DELETE /api/documents/{id}` and
`POST /api/documents/{id}/extract` plus `GET /api/jobs` and the
download-URL helpers. Uploads go through `XMLHttpRequest` (fetch has no
upload progress events); 200/201 resolve, 409/413/415/network reject
with `ApiError`. `src/api/taxonomy.ts` wraps the taxonomy list endpoints
(`GET /api/kinds|senders|tags`, docs/api.md §1.8.2), which feed the list
filters and the detail page's edit inputs — the former `DOCUMENT_KINDS`
hardcode is gone.

### 1.4.1 Documents list — `/` (`DocumentListView`)

GOV.UK-style search results: per row a thumbnail
(`/api/documents/{id}/thumbnail`, with a file-type placeholder when
`has_thumbnail` is false or the image 404s), title linking to
`/documents/:id` (carrying the active search as `?highlight=` so the
detail page can mark matches in the OCR text), kind and language tags,
sender, document date, and — when searching — the `ts_headline` snippet.
Search box (`q`, websearch syntax) plus a collapsible filter panel
(kind, sender and tag selects fed by the taxonomy endpoints, language,
date range using `GovDateInput`); `GovPagination` drives
`limit`/`offset` (25 per page). A one-shot success banner (Pinia
`useFlashStore`) confirms actions that redirect here, e.g. deletion.

All applied state lives in the **URL query**
(`?q=…&kind=…&sender_id=…&tag=…&language=…&date_from=…&date_to=…&page=…`),
so back/forward and refresh restore both the form and the results. Two
distinct empty states: an empty library (inset text linking to
`/upload`) vs. a search with no matches (inset text offering to clear
filters).

### 1.4.2 Document detail — `/documents/:id` (`DocumentDetailView`)

Two-column on desktop (preview left two-thirds, metadata right
one-third), stacked on mobile/iPad-portrait via the GOV.UK grid.

**Preview.** Images render as an `<img>` of the original. PDFs render in
an `<iframe>` using the **browser-native PDF viewer** — the searchable
PDF when the pipeline produced one (its text layer makes in-viewer
selection/search work), the original otherwise — plus an "open in a new
tab" link for browsers with inline PDF viewing disabled. This is a
deliberate choice over pdf.js: every modern browser ships a PDF viewer,
and a pdf.js integration would add a heavyweight dependency for no gain
at family scale. Other types get a fallback panel with a download link.

**Metadata editing.** A GOV.UK summary list with a per-row "Change"
action and an **inline reveal**: Change swaps the value cell for the
right input (GovInput for title, GovSelect for kind — options from
`GET /api/kinds` — and language, GovInput + `<datalist>` from
`GET /api/senders` for sender, GovDateInput for the three dates,
GovTextarea for summary, amount + 3-letter currency inputs) with
Save/Cancel. A full one-thing-per-page flow would be heavy for
single-field edits. Tags are edited as a **comma-separated GovInput**
for now — a deliberate simplification over a token/multi-select widget;
slugs are split, trimmed and sent as the full-replacement `tags` list.
Save PATCHes **only that row's field(s)** and replaces local state with
the server response (no optimistic updates); success shows a green
notification banner, a 422 shows a GOV.UK error summary linking to the
input and keeps the editor open. Empty rows display a dash. Status, OCR
confidence and source are read-only rows; extraction provenance (model,
confidence, when) sits in a GovDetails.

**Actions.** Download links for the original and (when present) the
searchable PDF; "Re-run extraction" POSTs `/api/documents/{id}/extract`,
shows an "Extraction queued" banner and polls the detail endpoint until
the extraction provenance changes (provenance JSON + count of
`extraction_*` audit events) or 60 s passes; Delete navigates to the
confirmation page.

**OCR text.** A "View extracted text" GovDetails with the raw OCR text
in a scrollable `<pre>`. When the page is reached from a search result
(`?highlight=`), occurrences are wrapped in `<mark>` by
`renderHighlighted` (see §1.5) and the details element starts open.

### 1.4.2.1 Delete confirmation — `/documents/:id/delete` (`DocumentDeleteView`)

GOV.UK pattern: destructive actions get a real **confirmation page**
with its own URL — warning text naming the document, an explicit
"Yes, delete this document" warning button (sends `DELETE`, CSRF header
included by `apiFetch`), and a Cancel link back to the detail page —
never a JS modal. On success the user is redirected to the list with a
one-shot success banner (flash store).

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

- Tag editing is a comma-separated text input, and the tag filter is a
  single-select (the API supports ANDed multi-tag filtering) — a richer
  tag widget is future polish.

## 1.5 Snippet safety

`GET /api/documents?q=…` returns `snippet`: `ts_headline` fragments over
**raw OCR text** with `<b>`/`</b>` highlight markers. The server does NOT
HTML-escape it — a scanned document can contain literal HTML
(docs/api.md §1.3.3). The frontend contract, implemented in
`src/utils/snippet.ts`:

1. `renderSnippet()` escapes *all* HTML-special characters, then
2. converts only the exact sequences `&lt;b&gt;` / `&lt;/b&gt;` back into
   real `<b>` / `</b>` elements.

`renderHighlighted(text, query)` follows the same contract for the
detail page's OCR-text view: every character of the input is escaped,
then occurrences of the (regex-escaped) query terms are wrapped in
`<mark>` — websearch operators (`OR`, `-exclusions`, quotes) are
stripped from the terms first.

These are the **only** strings ever bound with `v-html` (two annotated
sites: the list snippet and the detail OCR text).
`src/utils/__tests__/snippet.spec.ts` proves script tags, event-handler
attributes, attribute-smuggling through the markers, and
markup-smuggling through the query are all neutralised. Never bind raw
OCR-derived text — add new render sites only via these helpers.

## 1.6 End-to-end tests (Playwright)

Two spec files run against the **real stack**:
`frontend/e2e/library.spec.ts` (the W10 + W11 acceptance) and
`frontend/e2e/responsive.spec.ts` (the W16 viewport regression: no
horizontal overflow on `/login`, `/` and `/upload` — re-checked at the
320px floor on the mobile project — and the service navigation reachable
on every viewport, behind the Menu toggle below the GOV.UK 641px tablet
breakpoint, inline above it).

`library.spec.ts`: sign in → upload `e2e/fixtures/library-fixture.pdf` (a
checked-in one-page PDF whose text layer contains Dutch words, including
"rekeningen") → wait for `Indexed` (or the duplicate banner on re-runs)
→ see it in the list → search the stem `rekening` and assert a
highlighted snippet. The W11 test then creates a throwaway text document
with **unique content** via the API (deleting the shared PDF fixture
would break the other project's duplicate-upload path with a 409),
opens it from the list, edits the title through Change → Save (banner +
persistence across reload), and deletes it via the confirmation page
(banner on the list, detail then 404s). Claude extraction is not
required: assertions rely only on the OCR/text-layer pipeline, never on
extracted metadata, so the suite passes without an Anthropic API key.

Three projects — the W16 cross-device matrix, all running both spec
files: `chromium` (desktop), `mobile-webkit` (iPhone 14 device
descriptor pinned to the 375px acceptance viewport) and `tablet-webkit`
(iPad portrait). They run serially against one backend; the later
projects re-upload the same bytes and exercise the duplicate path.

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
  FileUpload init), API client CSRF behaviour, documents + taxonomy API
  (query serialisation, XHR upload incl. progress/duplicate/415/network),
  snippet + highlight XSS contracts, auth store, router guard, login view
  flows, document list (rows, empty states, URL-synced filters incl.
  sender/tag, taxonomy-fed options, highlight links, flash banner,
  pagination, snippet rendering), document detail (summary rows with
  dashes, per-field PATCH + banner, fetched kind options, sender
  datalist, 422 error summary, preview selection, OCR highlighting,
  re-extraction polling stop condition), delete confirmation flow,
  upload view (progress, polling, duplicate banner, error summary,
  multi-file independence), and the PWA wiring
  (`src/__tests__/pwa.spec.ts`: manifest linked/parseable/complete,
  icons exist, theme colours consistent, `viewport-fit=cover` kept —
  see §1.8).
- `npm run lint`, `npm run type-check`.
- `npm run build && npm run check:assets` — licensing gate (§1.2.1).
- `npm run test:e2e` — Playwright against the real stack (§1.6).

## 1.8 Mobile and PWA (W16)

The app is installable from the browser ("Add to Home Screen") so it
behaves like a scanning app on a phone, without a service worker — the
backend is the source of truth and offline caching of private documents
is deliberately out of scope.

Wiring (all asserted by `src/__tests__/pwa.spec.ts`):

- `public/manifest.webmanifest`, linked from `index.html`; `name`/
  `short_name` "Library", `start_url`/`scope`/`id` `/`.
- Icons are a hand-drawn black/white **"L." monogram** built from plain
  rectangles (`public/favicon.svg` is the source): no typeface is
  embedded and nothing resembles the crown/crest — `npm run
  check:assets` guards the bundle. Shipped as `icons/icon-192.png`,
  `icons/icon-512.png`, `icons/icon-512-maskable.png` (glyph inside the
  central safe zone), `apple-touch-icon.png` (180px) and `favicon.ico`.
- `theme_color` `#0b0c0c` = the masthead's `govuk-colour("black")`, and
  the `<meta name="theme-color">` in `index.html` matches it;
  `background_color` is white like the page body.

### 1.8.1 Display mode: `minimal-ui`, not `standalone`

The single most important journey on a phone is `/upload` →
`<input type="file">` → **camera or photo library**. iOS Safari in
`standalone` display mode has a history of file-input/camera quirks
(getUserMedia and file-capture regressions in standalone web apps across
several iOS releases), and standalone also hides the browser's
back/forward/reload affordances, which this multi-page-feeling app
relies on. `minimal-ui` keeps a minimal browser chrome — URL bar and
navigation stay available, the camera/photo-library sheet behaves
exactly as in Safari — while still being installable with its own icon.
(On iOS, cookies in home-screen web apps live in a container separate
from Safari's; the session cookie's 30-day lifetime applies per
container, so the first launch from the icon requires one sign-in.)

### 1.8.2 Safe areas and touch targets

- `index.html` sets `viewport-fit=cover`; `main.scss` pads the
  full-bleed bars (`app-masthead`, `govuk-service-navigation`,
  `app-footer`) with `env(safe-area-inset-left/right)` and the footer
  with `env(safe-area-inset-bottom)` for the home indicator.
- Touch targets are ≥44px (Apple HIG): GOV.UK buttons, form controls and
  pagination items (45×45px) are already compliant; the custom targets —
  masthead "Library" link, document-list title links, upload-row "View"
  links, standalone links (`app-standalone-link`: clear-filters,
  open-PDF, download links) and the summary-list "Change" buttons
  (`app-link-button`) — get a padded hit area via
  `padding-block`/negative `margin-block` so layout does not move.
- No horizontal scrolling down to the 320px floor (long OCR-derived
  strings are broken with `overflow-wrap: anywhere`); enforced by
  `e2e/responsive.spec.ts` (§1.6).

### 1.8.3 On-device checklist (real iPhone / iPad)

Automated coverage ends at WebKit emulation; before calling a release
mobile-done, walk this on real hardware against the deployed instance
(HTTPS — the session cookie is `Secure` outside the e2e override):

1. **Add to Home Screen** (Safari share sheet): the icon is the black
   "L." monogram, the label is "Library", and launching from the icon
   opens `/` with minimal browser chrome (URL bar present, no Safari
   tab bar).
2. **Sign in from the icon launch**, close the app, relaunch from the
   icon: still signed in (cookie persists in the web-app container;
   30-day lifetime).
3. **Camera capture**: `/upload` → "Choose files" → Take Photo →
   photograph a paper letter → upload reaches `indexed` and the OCR text
   is plausible.
4. **Photo library**: same flow via Photo Library, multi-select two
   images → two independent progress bars, both indexed.
5. **Safe areas**: on a notched iPhone, rotate to landscape — masthead
   and service-navigation text clears the sensor housing; portrait —
   footer text clears the home indicator. No horizontal scroll on
   `/login`, `/`, `/upload`, a document detail page, in either
   orientation.
6. **Touch**: pagination, the summary-list "Change" actions and the
   list-row title links are comfortably tappable; iPad portrait shows
   the inline navigation (no Menu toggle), iPhone shows the Menu toggle
   and it opens/navigates.
