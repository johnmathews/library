# Cross-browser PDF preview (self-rendered with pdf.js)

**Date:** 2026-06-22
**Status:** approved design, pending implementation plan
**Area:** `frontend/` — document detail page preview

## Problem

The document detail page (`/documents/:id`) previews PDFs in an `<iframe>` that
delegates rendering to the browser's own PDF viewer. Each engine behaves
differently, and the `#toolbar=0&navpanes=0&view=FitH` URL hints are advisory:

| Browser | Symptom | Cause |
|---|---|---|
| Chrome/Edge | "Pages / Manage" side panel overlapping the document | new viewer ignores `#navpanes=0` |
| Firefox | own toolbar + thumbnail sidebar | ignores `#toolbar=0` / `#navpanes=0` (code already clips the toolbar with a negative-margin hack) |
| Safari/WebKit | black square, no document | WebKit PDF-in-iframe paint bug |

Inconsistent, partly broken behavior across the three target engines is not
acceptable. The preview must look and behave the same in Firefox, Chrome, and
Safari, and must let the user **scroll through all pages**, not just see the
first page.

## Principle

The single root cause is delegating rendering to each browser's native viewer.
The only way to get identical behavior across engines is to render the PDF
ourselves to `<canvas>` with **pdf.js (`pdfjs-dist`)**. Same code path → same
result everywhere. No native viewer chrome is ever involved.

## Approach

Render PDFs in-app with `pdfjs-dist`, wrapped in a single self-contained Vue
component. Replace the iframe (and all its per-browser workarounds) with that
component on every viewport.

`pdfjs-dist` directly (not the lighter `vue-pdf-embed` wrapper) because we need
lazy per-page rendering and full control of the loading/error/password states —
a wrapper renders all pages eagerly and hides those hooks. The dependency stays
isolated behind the one component, so it remains swappable.

## Components

### 1. `frontend/src/components/DocumentPdfPreview.vue` (new)

Self-contained, independently testable.

**Props**
- `src: string` — inline PDF URL (`pdfPreviewUrl`, already carries
  `?disposition=inline`).
- `initialPage?: number` — page to scroll to on open (from the `?page=N` query
  param used by Ask citations).

**Behavior**
- Load the document with `pdfjs-dist`; render **all pages stacked vertically,
  fit-to-width**, inside a scroll container (`h-[70vh]`, `overflow-y-auto`).
- **Lazy page rendering** via `IntersectionObserver`: a page's canvas is painted
  only when it scrolls near the viewport. A 40-page PDF must not paint 40
  canvases at once (matters for Safari and mobile memory).
- Scroll to `initialPage` after layout when provided.

**State machine** (the component owns all of it)
- `loading` — show the existing server thumbnail (`thumbnailUrl`) as a poster
  plus a spinner.
- `rendered` — the scrollable page canvases.
- `error` — fallback card with **Open** / **Download** links (load or render
  failure).
- `password` — catch pdf.js `PasswordException` and show the existing padlock
  "Protected PDF — open to unlock" fallback (opening externally lets the browser
  prompt). No inline password entry in this iteration (YAGNI).

**Worker setup (Vite 8)**
```ts
import * as pdfjsLib from 'pdfjs-dist'
pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString()
```
Vite bundles the worker; it loads on demand, off the initial bundle.

### 2. `frontend/src/views/DocumentDetailView.vue` (modify)

Replace the entire `preview === 'pdf'` branch with one unified path for all
viewports:
```html
<DocumentPdfPreview :src="pdfPreviewUrl" :initial-page="pageParam" />
```

**Delete** (now dead, absorbed by the component):
- `pdfPreviewIframeUrl` (the `#toolbar=0&navpanes=0&view=FitH` fragment builder).
- `hidePdfToolbar` and the Firefox negative-margin clip (current lines ~583-590,
  ~855-866).
- The desktop `<iframe>`, the mobile thumbnail-link (`preview-pdf-image-link`),
  and the mobile locked-padlock special-case — the component handles loading,
  password, and error itself.

Keep `pdfPreviewUrl`, `previewOpenUrl`, `thumbnailUrl`, and the header
Open/Download buttons unchanged.

Net result: less code, one behavior on every engine and viewport, scroll-through
multi-page preview on both desktop and mobile.

## Dependency / bundle trade-off

Adds `pdfjs-dist` (~hundreds of KB; the worker is lazy-loaded on demand and does
not enter the initial bundle). This is the cost the original component docblock
deliberately avoided. The robustness + multi-page requirement justifies it.

## Testing — prove, don't assert

**Unit (Vitest / jsdom)** — `DocumentPdfPreview.spec.ts`
- Mock `pdfjs-dist` (canvas + worker do not run in jsdom).
- Assert the state machine: loading → rendered (N page slots for an N-page mock),
  load failure → error fallback with Open/Download, `PasswordException` → padlock
  fallback.
- Assert `initialPage` triggers a scroll to the right page.

**E2e (Playwright) — the actual cross-browser proof**
- Current matrix (`playwright.config.ts`): `chromium`, `mobile-webkit`,
  `tablet-webkit`. It has **no desktop Firefox and no desktop WebKit** — the gap
  that let these bugs ship.
- **Add desktop `firefox` and desktop `webkit` projects.**
- Add a preview spec that loads `frontend/e2e/fixtures/library-fixture.pdf` and
  asserts: page canvases render, and scrolling reveals a later page — running
  green in **chromium, firefox, and webkit**. That trio is the regression net.

## Out of scope (YAGNI)

- Inline password entry (external open covers it).
- Zoom controls / rotation / print toolbar (browser zoom + the existing Open
  button suffice; revisit only if asked).
- Continuous text-search across the document.
- Changing the image (non-PDF) preview path.

## Affected files

- `frontend/src/components/DocumentPdfPreview.vue` — new.
- `frontend/src/components/__tests__/DocumentPdfPreview.spec.ts` — new.
- `frontend/src/views/DocumentDetailView.vue` — replace the PDF preview branch,
  remove the iframe workarounds.
- `frontend/playwright.config.ts` — add desktop firefox + webkit projects.
- `frontend/e2e/pdf-preview.spec.ts` — new cross-browser preview spec.
- `frontend/package.json` — add `pdfjs-dist`.
- `docs/frontend.md` — document the self-rendered preview.
