# Sidebar search entry point

**Date:** 2026-07-15 · **Branch:** `worktree-eng-sidebar-search` · **Run:** engineering-team `manual-20260715T102434Z`

## 1. What changed

Search was only reachable from the nav bar (header magnifier button) and the global `/` shortcut, but the sidebar is where the eye intuitively looks for it. Added — not moved — a **Search** entry to the sidebar: a nav-item-styled `<button type="button" data-testid="sidebar-search-button">` placed after the pinned saved-view dashboards and before Upload, opening the same shared `SearchModal`.

Files:

1. `frontend/src/components/layout/AppSidebar.vue` — new `open-search` emit + the Search nav item (header's magnifier SVG, standard fading-label classes).
2. `frontend/src/layouts/DefaultLayout.vue` — `@open-search="searchModal?.open()"` on `<AppSidebar>`, mirroring the existing `AppHeader` wiring.
3. `frontend/src/components/layout/__tests__/AppSidebar.spec.ts` — two new tests (TDD: red first).
4. `docs/frontend.md` — sidebar/search sections updated; SearchModal now documented with its three entry points.

## 2. Decisions

1. **Button, not link** — search is a modal, not a route; no second modal instance, no search page.
2. **Emit up, wire in layout** — the modal stays a single instance owned by `DefaultLayout`; entry points stay stateless. Focus-return keeps working because `SearchModal.open()` records `document.activeElement`.
3. **`data-testid="sidebar-search-button"`** — deliberately a `-button` (analog of `header-search-button`), NOT `sidebar-*-link`, and not an `<a>`: the exact-nav-order tests in `AppSidebar.spec.ts` select `#sidebar-nav a[data-testid]`, so the button stays out of them **by design, not by accident**. The new position test asserts over `#sidebar-nav [data-testid]` (both element kinds) instead.
4. **No `@click.stop`** — the click deliberately bubbles through `#sidebar-nav`'s `close-sidebar` handler so the mobile drawer dismisses as the modal opens, matching nav-link behavior (unit-tested).
5. **No e2e addition** — mobile-webkit and tablet-webkit both run below `lg`, where the sidebar drawer is the established flaky zone; the search funnel is already e2e-covered via the always-visible header button (`library.spec.ts` `searchFor()`). Follow the hamburger-guard pattern if sidebar-search e2e coverage is ever wanted.

## 3. Discoveries / notes

1. The adversarial docs audit caught a stale line the initial doc update missed: `docs/frontend.md` §1.5 still said search was a "navbar-triggered modal" (plus "the modal entry point" in the header section implying a sole trigger). Both fixed. Historical docs (`docs/archive/`, `docs/superpowers/` plans/specs, dated journals) that say "search stays in the header" are point-in-time records and were correctly left untouched.
2. Minor a11y nuance accepted: on mobile, closing the modal returns focus to the opener button now inside the closed off-canvas drawer (off-screen but focusable). Changing that would mean touching `SearchModal`, an explicit non-goal.

## 4. Verification

Full frontend suite: **960 tests green** (958 baseline + 2 new), coverage 91.06% statements / 93.37% lines (gates 85%/75%), `eslint` and `vue-tsc` clean. Backend untouched.
