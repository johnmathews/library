# W10 — Frontend: document list, search, upload

**Date:** 2026-06-10. **Unit:** W10 (improvement plan §1.3.10).
**Depends on:** W7 (REST API), W8 (auth), W9 (design system).

## What landed

- **Typed API layer** — `frontend/src/api/documents.ts`: types mirroring
  `src/library/schemas.py` (`DocumentListItem`, `DocumentListResponse`,
  `DocumentDetail`, `DocumentFilters` with repeatable `tag`, `UploadResult`,
  `JobInfo`), `listDocuments`/`getDocument`/`listJobs`/`thumbnailUrl`, and
  `uploadDocument` over XMLHttpRequest (fetch still has no upload-progress
  events). 200/201 resolve (duplicate flag), 409/413/415/network reject as
  `ApiError`. CSRF constants exported from `client.ts` for reuse.
- **Documents list** (`DocumentListView`, replaces the W9 HomeView
  placeholder at `/`): GOV.UK-results rows (thumbnail with placeholder
  fallback, title link, kind/language tags, sender, date, snippet),
  search box, filter panel (kind, language, date range), `GovPagination`
  on limit/offset, two distinct empty states. All applied state is
  URL-synced (`?q=&kind=&language=&date_from=&date_to=&page=`).
- **Snippet safety** — `src/utils/snippet.ts` `renderSnippet`: escape all
  HTML, then restore only the exact `<b>`/`</b>` ts_headline markers.
  The single annotated `v-html` site in the app. Spec proves script tags,
  event handlers and marker attribute-smuggling are neutralised.
- **Upload page** (`/upload`): multi-file `GovFileUpload`
  (`accept="image/*,application/pdf"`, deliberately **no `capture`** so
  phones offer camera *and* photo library), per-file XHR progress on a new
  `AppProgressBar` (GOV.UK has no progress component — app extension with
  design-system colours, `role="progressbar"`), per-document status
  polling until indexed/failed, duplicate banner linking to the existing
  doc, error summary for 413/415/network. Files process independently.
- **Detail stub** (`/documents/:id`) — title + 4-row summary list; W11
  replaces it.
- **Navigation** — Documents + Upload in `GovServiceNavigation`.
- **GovDetails** gained an `open` prop (filter panel starts expanded when
  filters are active in the URL).

## E2E (the W10 acceptance)

Playwright (`frontend/e2e/`, config `playwright.config.ts`): sign in →
upload `e2e/fixtures/library-fixture.pdf` → indexed (or duplicate on
re-run) → listed → search the Dutch stem `rekening` (fixture text contains
"rekeningen") with a highlighted `<b>` snippet → no-results state for a
nonsense query. Two projects, run serially: chromium and a 375px
mobile-webkit pass (iPhone 14 descriptor, width pinned to the acceptance
viewport; Upload reached through the collapsed Menu toggle). Skips
entirely when `E2E_BASE_URL` is unset.

The fixture PDF is hand-built (checked in, 817 bytes) with a real
Helvetica text layer >50 chars/page so the pipeline takes the text-layer
path — verified with pypdfium2, the backend's own extractor. No Claude
key needed: assertions never touch extracted metadata.

CI: new `e2e` job — compose up `db migrate api worker` with the override
`frontend/e2e/compose.e2e.yml` (`LIBRARY_COOKIE_SECURE=false`; plain-HTTP
preview would otherwise lose the Secure cookie), create the `e2e` user via
`library user add --password-stdin`, build the frontend, serve with
`vite preview` (which inherits `server.proxy`, verified in vite source:
`proxy: preview?.proxy ?? server.proxy`), run Playwright, upload the HTML
report on failure, compose down.

## Decisions and gotchas

- **No taxonomy endpoints**: the backend has no `GET /api/kinds|senders|
  tags`, so sender/tag filters are absent and kind options are a
  hardcoded copy of the migration seed (`DOCUMENT_KINDS`). Flagged to the
  lead; docs/frontend.md §1.4.4 records the gap.
- govuk-frontend v6 FileUpload enhancement moves the `id` onto its
  drop-zone button — e2e must target `input[type="file"]`, not the id.
- GOV.UK type scale has no `14`; the thumbnail placeholder uses size 16.
- Status polling uses the document detail endpoint (per-doc, simpler than
  correlating `GET /api/jobs`); 2s interval, 3min cap, injectable via
  props for tests.
- The two e2e projects share one backend, so the second project
  deliberately exercises the duplicate-upload path (`workers: 1`).

## Verification

- `npm run test:unit -- --run` — 13 files, 91 tests passed.
- `npm run lint`, `npm run type-check`, `npm run build`,
  `npm run check:assets` — clean.
- Playwright against the local compose stack: **4 passed** (chromium +
  mobile-webkit × 2 specs); skip-mode verified (4 skipped without
  `E2E_BASE_URL`).
