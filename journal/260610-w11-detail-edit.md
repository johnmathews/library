# W11 — Frontend: document detail + metadata editing

**Date:** 2026-06-10/11. **Unit:** W11 (improvement plan §1.3.11).
**Depends on:** W10 (list/upload), W7 (REST API), W9 (design system).

## What landed

### Backend (small, deliberate additions)

- **Taxonomy endpoints** — `GET /api/kinds`, `GET /api/senders`,
  `GET /api/tags` (auth'd like the rest of `/api`), each
  `[{slug|id, name, document_count}, …]`; counts exclude soft-deleted
  documents, zero-count entries included; senders/tags ordered by name.
  W10 had flagged the gap (hardcoded `DOCUMENT_KINDS`, no sender/tag
  filters). The queries were extracted from the MCP `list_*` tools into
  a shared service, **`src/library/taxonomy.py`**, consumed by both the
  new REST router (`src/library/api/taxonomy.py`) and `mcp_server.py` —
  one implementation, no drift. (Side effect: MCP `list_tags` now orders
  by name instead of slug; its tests assert membership, not order.)
- **`POST /api/documents/{id}/extract`** — W6 created the
  `extract_document` Procrastinate task but no REST trigger existed.
  Returns `202 {queued, job_id}` after `defer_async`; 404 for
  unknown/deleted documents. Tests assert the real `procrastinate_jobs`
  row.

### Frontend

- **Detail page** (`/documents/:id`, replaces the W10 stub): GOV.UK
  grid, preview left 2/3 / metadata right 1/3, stacked below desktop.
  - *Preview:* images as `<img>`; PDFs in an `<iframe>` using the
    **browser-native viewer** (searchable PDF preferred, original
    otherwise, "open in new tab" link) — pdf.js rejected as a
    heavyweight dependency for no gain; fallback panel otherwise.
  - *Editing:* summary list with per-row Change → **inline reveal**
    (GOV.UK one-thing-per-page judged too heavy for single-field
    edits): GovInput/GovSelect (kinds from `/api/kinds`)/GovDateInput/
    sender GovInput + `<datalist>` from `/api/senders`/GovTextarea;
    tags as a **comma-entry input** (documented simplification). Save
    PATCHes only that row's field(s), awaits the response (no
    optimistic state), success banner; 422 → error summary linking the
    input, editor stays open.
  - *Read-only:* status, OCR confidence, source; extraction provenance
    (model, confidence, when from the audit trail) in a GovDetails.
  - *Actions:* original/searchable-PDF downloads; **Re-run extraction**
    → POST extract → "queued" banner → polls detail until the
    extraction fingerprint (provenance JSON + `extraction_*` event
    count — skipped runs change events but not provenance) moves, 60s
    cap; **Delete** routes to a confirmation **page**.
  - *OCR text:* GovDetails with scrollable `<pre>`; `?highlight=<q>`
    (passed by list search links) marks matches via a new
    `renderHighlighted` in `utils/snippet.ts` — same escape-everything
    contract as `renderSnippet`, XSS specs included.
- **Delete confirmation page** (`/documents/:id/delete`,
  `DocumentDeleteView`): warning text + confirm (DELETE w/ CSRF) +
  cancel back-link; success → redirect to list with a one-shot banner
  via a new Pinia `useFlashStore` (survives the redirect, not a
  refresh — deliberately not a query param).
- **List view:** kind options now fetched from `/api/kinds` (hardcode
  deleted), new sender + tag filter selects (`?sender_id=`, `?tag=`),
  detail links carry `?highlight=`, flash banner rendering.
- `GovInput` gained a `list` prop (datalist autocomplete);
  `warning-text` SCSS added (no restricted assets — verified by
  `check:assets`).

## Gotcha worth remembering

The W11 e2e test must NOT delete the shared PDF fixture document: the
two Playwright projects run serially against one backend and the second
project's upload asserts the *duplicate* path — deleting the document
would turn that into a 409 deleted-duplicate error. The test instead
creates a throwaway **unique-content** text document via
`page.request.post` (session + CSRF cookies from the browser context)
and edits/deletes that.

## Verification

- Backend: `uv run pytest -q` → **219 passed** (was 215; +4 taxonomy/
  extract, all MCP tests still green).
- Frontend: `npm run test:unit -- --run` → **121 passed** (16 files);
  `lint`, `type-check`, `build`, `check:assets` all clean.
- E2E against the real compose stack (db/migrate/api/worker + vite
  preview): **4 passed (8.4s)** — W10 upload/search and W11
  edit/delete in both chromium and mobile-webkit (375px).
