# Remove document-type quick-filter pill row from the dashboard

Date: 2026-07-20

## 1. Change

The dashboard filter bar (`DocumentFilterBar.vue`) had three stacked filter
surfaces below the search box:

1. The dropdown filter pills (Kind, Sender, Recipient, Date, Tag, Project,
   Matter, More).
2. A **document-type quick-filter row** — one round pill per kind with
   documents (Invoice 59, Receipt 15, …), ordered most-numerous-first with
   "Other" pinned last.
3. A **business-matter quick-filter row** — one pill per matter with documents.

Three rows made the top of the dashboard heavy. Removed row 2 (the
document-type row), keeping the dropdown pills and the business-matter row.

## 2. What was removed

- Template: the `data-testid="type-filters"` block.
- Script: the `typeFilters` computed and the `toggleKind` helper (both used
  only by that row), plus the now-unused `KindOption` import.
- Tests: the four `type-filter` specs in `DocumentFilterBar.spec.ts`.

## 3. No functionality lost

Kind filtering remains fully available through the **Kind** dropdown pill,
which still calls `selectKind` and renders every kind. The quick-filter row was
a convenience shortcut, not the only path.

## 4. Verification

- `npm run lint` — clean.
- `npm run type-check` — clean.
- `npx vitest run` — 1003/1003 pass (86 files).
- No `type-filter` references remain in `src/` or `e2e/`.
