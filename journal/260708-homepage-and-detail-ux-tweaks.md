# Homepage and detail UX tweaks

Four small, user-requested UI tweaks, done as one batch.

## 1. Persisted + better default homepage sort

- `DEFAULT_SORT` flipped `document_date` → `added_date` (still `desc`). "Newest
  added first" is the more useful default and, since `added_date` orders by
  `created_at desc, id desc`, it preserves the e2e "newest upload = first tile"
  invariant the other specs rely on.
- The sort choice is now **remembered** in `localStorage['library:doc-sort-v1']`.
  `setSort` writes it; `parseDocumentQuery(query, sortPref)` takes it as the
  fallback whenever the URL omits `sort`/`dir`, so a bare `/` reproduces the last
  selection (field *and* direction). An explicit URL param still wins; a
  garbage/unset preference falls back to the hard constants.
- The frontend default (`added_date`) differs from the API's default
  (`document_date`, shared with MCP — left untouched), so `buildFilters` now
  **always sends `sort` + `direction`** rather than omitting them at the
  frontend default (which would have silently ordered by the wrong field).

## 2. Sidebar "Saved views" → dashboard button

- Removed the standalone **Saved views** sidebar link. Pinned saved-view
  dashboards now sit **directly under Documents** with no heading.
- The management page (`/saved-views`) was reachable *only* via that sidebar
  link, so orphaning it was not an option. Per the request, access moved to a
  **Saved views** button in the dashboard controls row, next to the Save-view /
  Fields menus (`[data-testid="manage-saved-views-link"]`).

## 3. Confirm dialog centering

- The permanent-delete `ConfirmDialog` pinned top-left. Root cause: Tailwind
  Preflight resets `margin: 0` on every element, killing the native
  `dialog { margin: auto }` centering that `showModal()` relies on. The search
  modal already re-asserts it via `.app-search-modal { margin: auto }`; added the
  equivalent `.app-confirm-dialog` rule.

## 4. ActionDock margins + navbar alignment

- The floating dock sat flush against the header edge / viewport bottom. The
  pill row now insets from its rail (`top-4` / `bottom-4`) for a comfortable gap.
- Its horizontal padding changed `px-4` → `px-4 sm:px-6 lg:px-8` to match the
  navbar, so a left/right-anchored dock aligns with the header's outermost
  elements (full content width) rather than the narrower, max-width-capped main
  column. (Chose navbar alignment over content alignment per the request.)

## Verification

Full frontend unit suite (890) green, `vue-tsc` and `eslint` clean. Updated the
affected unit specs plus `docs/frontend.md`; adjusted `e2e/saved-views.spec.ts`
(its "non-default sort" fixture used `added_date`, now the default → switched to
`document_date`) and refreshed a stale ordering comment in `e2e/review-queue.spec.ts`.
The e2e suite needs the real Docker stack and runs in CI.
