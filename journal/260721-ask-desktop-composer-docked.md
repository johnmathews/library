# Ask desktop: dock the composer at the bottom

**Date:** 2026-07-21

## Problem

On desktop the `/ask` composer floated mid-page instead of sitting at the
bottom like a normal chat UI. The `#ask-page` two-pane panel had no bounded
height, so it grew to fit the taller of its two columns — the long
conversation-list sidebar. The thread column stretched to match, and its
`lg:sticky lg:bottom-0` composer only pins on overflow, so on a short
transcript it landed right after the last turn with empty space below.

## Fix

Mirror the already-shipped mobile fill pattern on desktop:

- The view root becomes a bounded-height flex column at `lg+`:
  `lg:h-[calc(100dvh_-_8rem)]` (viewport − the 4rem app header − `#app-page`'s
  4rem `py-8`). PageHeader / error summary are `lg:shrink-0`; `#ask-page` takes
  the rest with `lg:flex-1 lg:min-h-0 lg:overflow-hidden`.
- The transcript is now the internal scroll area on **every** breakpoint
  (`flex-1 min-h-0 overflow-y-auto`), dropping the `lg:pb-28` sticky-clearance.
- The composer is a plain `shrink-0` footer on every breakpoint — the
  `lg:sticky lg:bottom-0 lg:z-10` is gone. The sidebar already scrolled
  internally (`flex-1 min-h-0 overflow-y-auto`), so it now docks correctly too.

`scrollToBottom` already preferred the internal scroller when scrollable, so no
JS change was needed.

## Verification

- `AskView.spec.ts`: replaced the sticky-composer / page-scroll assertions with
  the footer-on-every-breakpoint + fixed-height-column contract; suite green (40).
- `vue-tsc`, eslint, and `vite build` clean.
- Confirmed the compiled CSS emits `calc(100dvh - 8rem)` with spaces (the
  underscore-to-space rule that iOS Safari requires — see the earlier
  260721-ask-mobile-height-calc entry).
