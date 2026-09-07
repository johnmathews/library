# Metadata panel: titles, collapsible subsections, and consistent spacing

**Date:** 2026-09-06

## 1. What & why

Follow-up to the same day's panel consolidation
(`260906-consolidate-document-detail-panels.md`), from actually looking at the
result. Four things were wrong, and all four were only visible once the panel
existed:

1. **`amount` rendered at `text-2xl font-semibold`** in the hero's stat grid,
   beside Kind and Sender at `text-base`. That styling was correct when
   **Financial** was a card of its own showing one large number; once `amount`
   moved to the hero it was just inconsistent. With the Financial group gone the
   branch could *only* ever fire in the hero.
2. **No spacing between subsections.** Each was a bare `min-w-0` div — no gap,
   no separator — so a heading butted straight onto the previous group's last
   field. "MATTERS / Subscriptions" ran directly into "SENDER, RECIPIENT &
   DATES" with nothing between them, and the structure was invisible.
3. **`Sender, recipient & dates` was one group**, putting two unrelated kinds of
   fact under one heading and burying the dates.
4. **The first subsection's heading read as the panel's title.** With nothing
   above it, "CONTENT" looked like it labelled everything below.

## 2. What shipped

- The panel is titled **Metadata**, and the first group is renamed
  `content` → `classification` (it holds language, tags, projects, matters).
  Two things named at two levels, instead of one heading doing both jobs.
- `parties` (sender, recipient) and `dates` (document/due/expiry) are separate.
- One `SECTION_WRAPPER` class string on every section — a top rule plus equal
  padding, dropped on the first via `first:` — so the panel has one vertical
  rhythm instead of each group setting its own margins.
- Every heading is a `button` with `aria-expanded`/`aria-controls` that folds
  its section. Collapsed keys persist per-machine; **Reset layout** expands all.
- `amount` renders at the same size as every other field.

## 3. Decisions worth recording

**Collapsed state is a key LIST, not a per-key boolean map.** A map would need a
migration every time a section is added, and an absent key would have to mean
"expanded" anyway — so the list makes "everything expanded" the default for a
new user and a returning one alike, for free. `reconcileCollapsedSections`
drops keys that are no longer sections; `content` → `classification` is the
first case it handles.

**`v-if`, not `v-show`, for the folded body.** The first draft used `v-show` and
the test failed in a revealing way: `aria-expanded` had flipped to `false` while
`isVisible()` still reported the row visible. That is the known jsdom trap with
`v-show` ancestors. `v-if` makes "collapsed" mean *absent*, which is both the
cleaner semantic and a non-flaky assertion (`exists()`). Nothing is lost by
unmounting, because fields autosave on commit rather than holding a long-lived
draft.

**An empty group is dropped, not rendered as a heading over nothing.** With the
shipped hero defaults that is exactly what happens to `parties`: sender and
recipient are both hero-owned, so the section is legitimately absent. My first
test asserted all five sections always render and was wrong, not the code — the
test now asserts the four that render by default, plus a second case that brings
`parties` back by hiding sender from the hero.

## 4. Verification

Frontend suite **1464 passed**; `eslint` and `vue-tsc` clean; `check_docs` ok.
All four behaviours were **observed red** against the previous commit before
being fixed.

**Not verified:** no browser was driven — the local e2e stack could not be
raised (`docker compose` binds `8000:8000`, and port 8000 is held by an
unrelated process). So the *spacing* is asserted as a shared class string, never
as rendered layout: jsdom computes none. CI's `detail-layout.spec.ts` overflow
sweep on `mobile-webkit` remains the only runtime check of this panel's
geometry, and it does not check that the rhythm looks right — only that nothing
overflows.

## 5. Gotcha for the next person

Running `npx vue-tsc --build` in a **fresh worktree** emitted 244 compiled
`.js`/`.d.ts` files next to the sources. Vitest then collected them as extra
specs and the suite silently jumped from 112 files to 224, with a duplicate
`.spec.js` failing for reasons that had nothing to do with the change. They are
untracked build artifacts — delete them before committing.
