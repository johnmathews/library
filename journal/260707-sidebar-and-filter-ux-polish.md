# Sidebar IA + homepage filter UX polish

Follow-up tweaks after testing the Recently-Deleted / Saved-Views features live
on a phone.

## 1. Changes

1. **Homepage filters always visible + wrapping.** `DocumentFilterBar` no longer
   collapses the pill row behind a "Filters" toggle below `sm`. The pills are
   always shown and `flex-wrap` onto multiple rows on narrow screens. Removed the
   toggle button and its now-dead `filtersExpanded` / `toggleFilters` /
   `activeFilterCount` state.
2. **Result count on its own row.** On `DocumentListView`, the "N documents"
   count stacks above the sort/tiles/save-view controls on mobile
   (`flex-col sm:flex-row`), and the controls row `flex-wrap`s, so nothing is
   clipped on a phone.
3. **Sidebar information architecture** (per request):
   - **Saved views** moved to right after **Documents** (position 2).
   - **Pinned dashboards** are now **first-class nav links** directly under
     Saved views — the separate "DASHBOARDS" subsection (and its
     `sidebar-dashboards-section` wrapper) is gone.
   - **Recently Deleted** moved to the **bottom** of the nav (after Admin), as a
     low-traffic destination.

## 2. Tests / docs

1. Updated `AppSidebar.spec` (new nav order; pinned dashboards assert as
   first-class links after Saved views; no-subsection case). Replaced the three
   `DocumentFilterBar` collapse-toggle tests with one asserting the pill row is
   always visible + wraps and the toggle is gone.
2. Frontend **861 passed**, eslint + `vue-tsc` clean.
3. `docs/frontend.md`: updated the AppSidebar nav-order list, the filter-bar
   "always visible / wraps" behaviour (two spots), and the SavedViewsView row
   (pinned views are first-class links, not a subsection).

## 3. Notes

The e2e `openFilters` helper (`projects.spec`) degrades gracefully: the missing
`filter-toggle` locator reports not-visible, the click is skipped, and the
always-visible `filter-pills` assertion still holds.
