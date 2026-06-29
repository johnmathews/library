# Recipient management in the admin panel

Date: 2026-06-29. Branch: `worktree-eng-recipient-admin-management`. Follow-on to the recipient field
(merged at `689caac`); run dir `.engineering-team/runs/manual-20260629T163545Z/`. Single work unit (W1).

## 1. What shipped

An admin-only management UI for recipients (the first taxonomy CRUD in the project) so family members
can be renamed, merged, and removed without a migration.

- **Backend** (`src/library/api/admin.py`, `src/library/taxonomy.py`):
  - `PATCH /api/admin/recipients/{id}` — rename. On a case-insensitive name collision with another
    recipient, returns 409 (carrying the target's id/name/document_count) unless `merge: true`, in which
    case it reassigns this recipient's documents to the target and deletes this row.
  - `DELETE /api/admin/recipients/{id}` — reassign-then-delete via a three-state `reassign_to` query:
    omitted → 409 guard if the recipient still has documents; `=<id>` → move documents to that recipient
    then delete; `=` (empty/null) → clear documents' recipient then delete.
  - Both gated by `require_admin` (the `/admin` router), mirroring the users CRUD. Services live in
    `taxonomy.py` returning status objects so HTTP concerns stay in the route.
- **Frontend** (`frontend/src/views/AdminView.vue`, `frontend/src/api/{admin,client}.ts`):
  - A new **Metadata** admin tab listing recipients with document counts; per-row inline rename (with a
    merge-warning confirm step on collision) and delete (with an inline reassign picker when documents
    exist). No blocking `window.confirm`/`prompt`.
  - `ApiError` extended (backward-compatibly) to expose the parsed error body so the rename flow can read
    the 409 conflict fields. After any mutation, `refreshTaxonomyOptions()` keeps document dropdowns/filters
    elsewhere current.

## 2. Key decisions

1. **Delete in use → reassign then delete** (user choice). The `reassign_to` three-state encoding (an
   `UNSET` sentinel vs explicit null vs id) is what distinguishes "refuse if in use" from "clear and
   delete" — FastAPI's typed `int | None` query param can't express that, so the route inspects the raw
   query string.
2. **Rename collision → merge with a warning first** (user choice). Merge bypasses `user_edited_fields`
   locks deliberately (the recipient row is being deleted, so its documents *must* move). Note this isn't
   permanent for *auto-extracted* (non-locked) recipients — a later re-extraction can re-create a
   merged-away name; acceptable and documented.
3. **Recipients-only CRUD.** Did not generalize management to kinds/senders/tags this round.

## 3. Bug found in review and fixed

The 409 conflict bodies were raised via `HTTPException(detail={...})`, which FastAPI double-wraps as
`{"detail": {...}}` — but the routes' own OpenAPI declared a *flat* conflict model and the frontend read
the fields flat, so the merge-confirmation warning rendered "merged into **undefined** (**NaN**
documents)" before the user confirmed a destructive merge (the merge itself still completed correctly).
The unit test missed it by constructing a flat `ApiError` body that bypassed the real parser. Fixed by
returning a flat `JSONResponse` for both 409s (rename + delete), making implementation, OpenAPI, frontend
types, and docs all agree; updated the backend tests to the flat shape (the frontend test was already
realistic and asserts the real target name + count).

Review otherwise verified transaction atomicity, the self-excluding case-insensitive collision check,
soft-deleted-row handling in the reassign UPDATE, admin gating, `ApiError` backward compatibility, and
that recipient names are text-interpolated (no `v-html` / XSS).

## 4. Tests & docs

- Backend: **699 passed** (incl. integration); 16 new admin-recipient tests (rename/casing/collision/
  merge, delete zero-doc/in-use/reassign/null, self-reassign 400, unknown 404, non-admin 403, anon 401).
  ruff clean.
- Frontend: **469 passed**; 6 new AdminView tests (panel render, rename, merge-confirm, delete zero-doc,
  delete-with-reassign). type-check + lint clean.
- Docs: `docs/admin.md` (Recipient management section + endpoints + 5th tab), `docs/api.md`
  (`PATCH`/`DELETE /api/admin/recipients/{id}` with flat 409 bodies), `docs/frontend.md` (Metadata tab).
