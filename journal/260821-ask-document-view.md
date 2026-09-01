# Ask: a document view for the transcript

**Date:** 2026-08-21

## Problem

The `/ask` transcript renders every turn as a chat bubble: the question
right-aligned in violet at `max-w-[85%]`, the answer beneath it. That reads well
for a two-line reply and badly for the answers Ask actually produces most of the
time — multi-section prose with GFM tables, which is what the whole retrieval
engine exists to generate.

The request was to borrow the "document view" from `homelab-sre/sre-webapp`,
keeping this app's colours, fonts and styling.

## What was already here

Three of the four things the borrow was supposed to bring turned out to be
present already, which changed the shape of the work:

- The conversation rail already has the reference's ordering — New conversation,
  search, scrolling thread list (`ConversationSidebar.vue`). The reference's
  extras are a session-id chip and a health strip, neither of which has an
  analogue here.
- The composer is already a docked `shrink-0` footer, deliberately so
  (`260721-ask-desktop-composer-docked.md`), and `AskView.spec.ts` asserts it.
  The reference's composer is *also* inline rather than floating; what the
  screenshots actually show is centring, not docking.
- `.ask-answer` already styles GFM tables with borders, header weight and a
  header tint — better than the reference, which has no `th` rule at all.

## The measurement that changed the design

The reference clamps document mode to `lg` (1024px). It can afford to: it has one
sidebar. This app has two — the global `AppSidebar` and the in-page conversation
rail — and the global one *defaults to expanded* at ≥1024px.

Measured in Chromium against the built CSS, with a harness reproducing the real
DOM chain:

```
viewport | sidebar    | thread-pane | answer text
    1024 | expanded   |         414 |         332
    1024 | collapsed  |         590 |         508
    1280 | expanded   |         670 |         588
    1440 | expanded   |         830 |         748
```

332px is narrower than the 375px phone viewport `playwright.config.ts` pins as
this app's mobile acceptance width. Shipping the reference's breakpoint verbatim
would have given every user between 1024 and ~1280px a "document" layout
narrower than the phone layout it replaces, minus the right-alignment that makes
turn boundaries legible in a narrow column.

So document mode **collapses the conversation rail**, returning its 288px. After
the change, same harness: 620px at 1024, 876px at 1280, 990px at 1440. The rail
stays when no conversation is open — otherwise the "select a conversation from
the sidebar" empty state would point at a sidebar that isn't there.

## The table bug, and why it was mis-predicted

The evaluation predicted a wide table would be silently clipped by `#ask-page`'s
`overflow: hidden`. Probing computed styles instead of assuming showed something
different and worse: the transcript is `overflow-y-auto`, and CSS computes the
other axis to `auto` when one axis is not `visible`. So the **entire transcript
panned sideways** — every question bubble moving with the table — while the part
past the panel was clipped. Measured at 1024px: transcript `scrollWidth` 482
against a `clientWidth` of 414.

The fix is the reference's, and it is one mechanism in three parts that only
works whole:

1. `wrapTables()` post-processes the rendered HTML, wrapping each `<table>` in a
   `.ask-table-wrap` with `overflow-x: auto`, `role="region"` and `tabindex="0"`.
   It runs **before** `DOMPurify.sanitize` so the wrapper is sanitised like
   everything else, and `tabindex` needs an explicit `ADD_ATTR` — it is not in
   DOMPurify's default allow-list, so without it the keyboard affordance is
   stripped silently while the table still looks fine.
2. `table { width: max-content }` replaces `width: 100%`, so columns take their
   natural width instead of being crushed to single characters.
3. `min-width: 0` on `.ask-answer`, because a flex item defaults to
   `min-width: auto` — its intrinsic content width — and would otherwise push the
   column past its basis regardless of the wrapper.

After: `transcript pans? false` at every width tested.

## Composable shape — not the reference's

`useViewMode.ts` in the reference is a module-level singleton that runs
`matchMedia` and registers an unremoved listener at import time. That is fine
there — that app has no unit test runner at all. Here it would be the first time
that design met a shared module cache, and the failure mode is order-dependent
tests that pass alone and fail in suite.

`useAskViewMode.ts` follows `useMarkdownEditorMode.ts` instead: `useStorage`
constructed inside the function, key `library:ask-view-mode` per
`frontend-view-principles.md` §4, and the `lg` clamp takes the caller's
`isLargeScreen` ref rather than opening a second media query. `AskView.vue`
already had one for the Enter-key rule, so the toggle can never disagree with it
about what "desktop" means. The spec needs only `localStorage.clear()` — no
`vi.resetModules()`.

The reference's one genuinely good idea is kept: two refs, not one. `viewMode` is
the stored preference; `effectiveViewMode` is the clamped render decision. A
desktop user's choice is **clamped, not overwritten**, so opening the app on a
phone doesn't silently discard it.

## Folded in while here

- The composer's file input had no programmatic label — one of four sites
  `eslint.config.ts` names as blockers to re-enabling `form-control-has-label`.
  Fixed; the ratchet comment now names three.
- `.ask-answer` had no `pre` rule, so fenced code blocks overflowed.
  `DocumentDetailView` has had that fix for months; it was never back-ported.
- The citations grid used `md:grid-cols-2` — a *viewport* query inside a pane
  that is 332px wide at that viewport, so it went two-up in a column too narrow
  for it. Now a container query on the answer surface (`@container` +
  `@lg:grid-cols-2`), which measures the pane.
- `thin-scrollbar` on the transcript. `utility-patterns.css` names the Ask
  transcript as the motivating case for that class; only the sidebar ever
  adopted it.

## Verification

- `vitest run --coverage`: 89 files, 1081 tests green (was 88/1059). Coverage
  90.35 / 82.82 / 89.23 / 92.73 against the 85/85/85/75 floor.
- `eslint .`, `vue-tsc --build --force`, `vite build`: all clean.
- `check_docs.py --max-violations 0`: 17 documents stamped and verified.
- The three table-wrapping tests were confirmed to **fail with the fix reverted**
  — the fourth is a negative control and correctly passes either way.
- Layout re-measured in Chromium before and after; numbers above.
- `e2e/ask-document-view.spec.ts` (5 tests × 3 projects) **collected but not
  run** — it needs the compose stack via `E2E_BASE_URL`. It runs in CI's `e2e`
  job, which gates `promote`.
