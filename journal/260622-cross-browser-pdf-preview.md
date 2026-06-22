# 2026-06-22 — Cross-browser PDF preview (self-rendered pdf.js)

## Context

The document detail page (`/documents/:id`) previewed PDFs in a native
`<iframe>`, delegating rendering to each browser's built-in viewer with
`#toolbar=0&navpanes=0&view=FitH` hints. Those hints are advisory, so the
result diverged per engine — discovered by testing the same document in three
browsers:

- **Chrome/Edge:** forced a "Pages / Manage" page-organizer panel overlapping
  the document (new viewer ignores `#navpanes=0`).
- **Firefox:** rendered its own toolbar + thumbnail sidebar (ignores the
  fragments; the code already clipped the toolbar with a Firefox-only
  negative-margin hack).
- **Safari/WebKit:** a black box — WebKit's long-standing PDF-in-iframe paint
  bug.

Three different broken behaviors, one root cause: we weren't rendering the PDF,
the browser was.

## Decision

Render the PDF ourselves with **pdf.js (`pdfjs-dist`)** to `<canvas>` — the only
engine-independent way to get identical output. Chosen over the lighter
`vue-pdf-embed` wrapper because we need lazy per-page rendering and full control
of the loading/error/password states; the dependency stays isolated behind one
component. Accepted the bundle cost (worker lazy-loaded, off the initial bundle)
that the original docblock had avoided — robustness was the explicit priority.

## What shipped

`frontend/src/components/DocumentPdfPreview.vue` — loads the PDF, renders every
page stacked fit-to-width in a scroll container, paints each canvas lazily via
`IntersectionObserver` (300px look-ahead), `devicePixelRatio`-aware. Four-state
machine: loading (faded thumbnail poster), rendered, error and password (padlock)
fallbacks with Open/Download. Decoupled from the API — URLs arrive as props.
`DocumentDetailView.vue` mounts it on **all** viewports, deleting the iframe,
`pdfPreviewIframeUrl`, `hidePdfToolbar`, and the mobile thumbnail/padlock
special-cases. Playwright matrix gained desktop `firefox` + `webkit` projects and
`e2e/pdf-preview.spec.ts` (renders a 2-page fixture, asserts canvases paint and
scrolling reveals page 2).

## Notable findings during build

- `PDFDocumentProxy` has **no** `destroy()` method (verified against the
  installed v6 types) — the first-draft cleanup `pdf.destroy()` was a latent type
  error the mocked unit tests couldn't catch. Cleanup uses `loadingTask.destroy()`.
- Added a generation counter to `load()` so a stale resolution from rapid `src`
  changes (fast document navigation) can't commit the wrong PDF.
- `renderPage` marks a page rendered only *after* confirming its canvas exists,
  so a not-yet-mounted canvas retries instead of staying permanently blank; and an
  eager-render fallback covers the (theoretical) no-`IntersectionObserver` case.

## Outstanding

**Run `frontend/e2e/pdf-preview.spec.ts` green against the compose stack**
(`E2E_BASE_URL`, recipe in `docs/frontend.md §1.5`) across chromium/firefox/webkit.
The unit tests mock pdfjs entirely, so the actual render path is currently proven
only by inspection — the e2e is the real cross-browser proof and hasn't executed
green yet. Highest-value follow-up.

Worked through the brainstorming → spec → plan → subagent-driven execution flow;
final whole-branch review: ready to merge, no Critical/Important. Merged to `main`
as `0828a0f`.
