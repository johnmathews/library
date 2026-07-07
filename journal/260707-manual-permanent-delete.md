# Manual permanent delete + read-only trash detail (bug fix)

A small engineering-team cycle (evaluate → plan → develop → wrap-up) closing two
gaps in the Recently-Deleted flow shipped on [260706](260706-recently-deleted-and-saved-views.md):
a broken title link and the absence of an on-demand hard delete.

## 1. What shipped

### 1.1 Bug: clicking a deleted title 404'd

`RecentlyDeletedView` linked each card title to the standard detail route
(`document-detail` → `/documents/:id`), but `GET /api/documents/{id}` calls
`_get_document_or_404`, which 404s any document with `deleted_at` set — the
invariant every list/search path relies on. So every title click in Recently
Deleted landed on the "Document not found" page.

Fix: an **opt-in** read path rather than relaxing the global invariant.

1. `GET /api/documents/{id}?include_deleted=true` (and the same flag on
   `…/markdown`) returns a soft-deleted document instead of 404ing, with the new
   `deleted_at` field on `DocumentDetail`. Default stays `false`; only the detail
   view passes it.
2. `DocumentDetailView` fetches with `includeDeleted: true`, and when
   `deleted_at` is set renders a read-only **trash banner** (Restore /
   Delete permanently) and hides the soft-delete link. `loadMarkdown` forwards
   the flag so the reader isn't blank.

### 1.2 Feature: on-demand permanent delete

Hard delete previously existed *only* as the daily `purge_deleted_documents`
cron. Added `DELETE /api/documents/{id}/permanent` (204): hard-deletes a
document that is **already in the trash** (`_get_deleted_document_or_404`, so it
404s a live/unknown doc — you must soft-delete first, mirroring restore). It
reuses the purge job's ordering — delete row → commit → `storage.remove(sha256)`
— so a failed unlink leaves at worst a reclaimable orphan file, never a live row
with a missing file.

UI: an inline confirm dialog. A new reusable `ConfirmDialog` (native `<dialog>`,
same convention as `SearchModal`) is wired into both a per-card
"Delete permanently" button in `RecentlyDeletedView` and the detail-view trash
banner. Confirming purges and (from the detail view) navigates back to `/deleted`.

## 2. Design decisions

1. **Opt-in, not a relaxed invariant.** `include_deleted` defaults off, so
   lists, search, taxonomy, series, downloads — every other read path — keep
   404ing deleted docs. Only the explicit-id detail read can opt in.
2. **Permanent delete only targets the trash.** Guarded by
   `_get_deleted_document_or_404`; a live document must be soft-deleted first, so
   the endpoint can never one-step nuke a live document.
3. **One `ConfirmDialog`.** Both the card and the banner need the same
   irreversible-action confirmation, so it was built once.

## 2b. Two fixes from the wrap-up code review

1. **Read-only actually means readable.** The first pass only added
   `include_deleted` to the detail + markdown endpoints, so a trashed *PDF or
   image* opened read-only but its preview/download 404'd (`original`,
   `searchable.pdf`, `thumbnail` still rejected deleted docs). The flag now
   threads through all three file endpoints and their frontend URL builders
   (`originalUrl` / `searchablePdfUrl` / `thumbnailUrl` gained an
   `includeDeleted` option), and `DocumentDetailView` passes it when the doc is
   deleted — so the preview pane and download links resolve.
2. **Cancel can't lie.** `ConfirmDialog`'s Cancel button, backdrop click, and ESC
   were live while a confirmed delete was in flight — clicking Confirm then
   Cancel closed the dialog as if nothing happened while the DELETE still
   completed (and in the detail view, navigated the user to `/deleted`). Cancel /
   backdrop / ESC are now no-ops while `busy`, and the Cancel button is disabled.

## 3. The regression test the user asked for

`frontend/e2e/recently-deleted.spec.ts` gained a test that soft-deletes a doc,
opens `/deleted`, **clicks the title**, and asserts the detail page loads (trash
banner visible, no "Document not found") — the exact path that was broken. A
second e2e covers the permanent-delete-with-confirmation flow. Backing them:
`test_detail_include_deleted_returns_soft_deleted` and the
`test_permanent_delete_*` cases (backend), plus `ConfirmDialog.spec` and new
`RecentlyDeletedView` / `DocumentDetailView` unit tests.

## 4. Verification

Full backend suite 996 passed; frontend unit 883 passed; `vue-tsc`, `eslint`,
and repo-wide `ruff format --check` / `ruff check` all clean. E2e self-skips
locally (needs the compose stack) and runs in CI, which gates promote.

## 5. Touched surfaces

- Backend: `api/documents.py` (permanent endpoint + `include_deleted` on detail,
  markdown, original, searchable.pdf, thumbnail + `_get_document_or_404` flag),
  `schemas.py` (`DocumentDetail.deleted_at`).
- Frontend: `api/documents.ts` (`getDocument` opts, `fetchDocumentMarkdown` opts,
  `permanentlyDeleteDocument`, `deleted_at`, `includeDeleted` on the file URL
  builders), `components/app/ConfirmDialog.vue`, `views/RecentlyDeletedView.vue`,
  `views/DocumentDetailView.vue`.
- Docs: `docs/api.md` (§1.1 table, §1.4/§1.4.1 `include_deleted`, §1.6 permanent
  delete), `docs/frontend.md` (`ConfirmDialog` row, RecentlyDeleted +
  DocumentDetail rows).
