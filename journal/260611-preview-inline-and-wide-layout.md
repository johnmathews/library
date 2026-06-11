# Preview inline disposition + wide-desktop layout

**Date:** 2026-06-11

Two user-reported fixes.

## 1. Document preview was blank and triggered an immediate download

**Symptom.** Opening `/documents/:id` showed an empty preview panel and
the browser downloaded the file instead.

**Root cause.** `GET /api/documents/{id}/original` and
`…/searchable.pdf` always sent `Content-Disposition: attachment`
(FastAPI's `FileResponse(filename=…)` defaults to attachment). The
detail page embeds those same URLs in an `<iframe>` (PDFs) and `<img>`
(images): an attachment response inside an iframe renders nothing and
triggers a download, and Firefox refuses to render `<img>` sources
served as attachment.

**Fix.** Both endpoints now take `?disposition=inline|attachment`
(default `attachment`, so every existing download link is unchanged;
validated as a `Literal` query param → 422 on anything else). Inline
uses `FileResponse(content_disposition_type="inline")`, keeping the
filename in the header so a user-initiated "save" still gets the right
name. The thumbnail endpoint needed no change: it passes no `filename`,
so Starlette emits no `Content-Disposition` header at all and browsers
already render it inline.

Frontend: `originalUrl(id, options?)` / `searchablePdfUrl(id, options?)`
gained an optional `{ inline?: boolean }` that appends
`?disposition=inline`. The detail page's iframe, preview `<img>` and
"open the PDF in a new tab" link use inline URLs; the Actions download
links and the no-preview fallback's download link keep the attachment
default.

## 2. Content column too narrow on wide desktops

GOV.UK's `govuk-width-container` caps at `$govuk-page-width` (960px) —
fine for text-led gov.uk pages, but the documents list and the detail
page's two-thirds preview pane swam in whitespace on large monitors.

**Fix.** A documented design-system extension in `main.scss`
(docs/frontend.md §1.2.5): at `min-width: 1400px` the container's
`max-width` rises to 1280px. A media-query override was chosen over
setting `$govuk-page-width` on the Sass module because the Sass setting
would widen the container at every viewport from ~990px up; the
override leaves GOV.UK's responsive behaviour below 1400px untouched.
The percentage-based grid keeps the detail split proportionate
(~853px preview / ~427px metadata at 1280px).

## Tests

- `tests/test_documents_api.py`: inline/attachment/default/422 coverage
  for both endpoints (written first, red → green).
- `frontend/src/api/__tests__/documents.spec.ts`: URL helper contract.
- `frontend/src/views/__tests__/DocumentDetailView.spec.ts`: iframe/img
  and open-in-new-tab carry `?disposition=inline`; download links don't.
- `frontend/e2e/responsive.spec.ts`: chromium-only 1920×1080 check —
  main content container wider than 1100px, no horizontal overflow.
