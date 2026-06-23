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

Worked through the brainstorming → spec → plan → subagent-driven execution flow;
final whole-branch review: ready to merge, no Critical/Important. Merged to `main`
as `0828a0f`.

## Ship: CI cross-browser proof, then deploy

Pushing `main` turned the "outstanding e2e" item into the real proof — but only
after CI exposed three integration issues the no-stack build couldn't:

1. **CI didn't install Firefox.** Task 5 added a desktop `firefox` Playwright
   project, but `ci.yml` installed only `chromium webkit`, and the `promote`
   job (which retags `:latest`, the deployed image) `needs: [e2e]`. So the e2e
   job would fail launching Firefox and block the deploy. Fixed the install
   (`chromium firefox webkit`) and **scoped the firefox/webkit-desktop projects
   to `pdf-preview.spec.ts` via `testMatch`** so adding these engines didn't put
   the whole suite on Firefox.
2. **`pdf-preview.spec.ts` 409 on every project after chromium.** The five
   matrix projects run serially and the backend dedups by *content hash*; the
   per-project filename marker didn't change the bytes, so only chromium got
   201. Fixed by appending the marker as a trailing PDF comment for unique bytes
   (the `library.spec.ts` trick).
3. **`ask-page-citation.spec.ts` asserted the removed `<iframe>`'s
   `src=/page=2/`.** Task 4 deleted the iframe; updated the spec to assert the
   `?page=2` deep-link mounts the component instead.

With those fixed, CI went green: `pdf-preview.spec.ts` **passed on chromium,
firefox, and webkit** — the real cross-browser proof, by execution. `promote`
retagged `:latest`.

**Deployed** to the `paperless` LXC (`/srv/apps`, compose project `apps`):
`docker compose pull` + `up -d library-webserver library-worker` (migrate ran
clean — no new migrations for a frontend-only change). Healthcheck green,
`/healthz` → `{"status":"ok","version":"0.1.0"}`, and the live server serves the
new bundle with `pdf.worker.min-*.mjs`. The three engines that each broke a
different way are now confirmed identical in CI and in production.
