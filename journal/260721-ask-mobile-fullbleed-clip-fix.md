# Ask mobile: fix the full-bleed panel being clipped by the fill wrapper

**Date:** 2026-07-21

## Problem

On the mobile chat screen (`/ask/:threadId`) the white conversation area did
not reach the screen edges: grey gutters on the left and right, and the first
character of each answer line was clipped ("aterleidingbedrijf", "laes", …).

## Cause

`#ask-page` runs edge-to-edge by breaking out of `#app-page`'s `px-4 sm:px-6`
with `-mx-4 sm:-mx-6`, so it is ~16px wider than its parent on each side. The
mobile fixed-height fill wrapper (`chatFillClass`) carried `overflow-hidden`,
which clips *painting* to its (padded) box — chopping those 16px off both sides.
The panel then rendered shifted 16px left and 16px short on the right, exactly
matching the reported gutters + left-edge text clip.

(Pre-existing since the fill column landed in #33; surfaced now on a real chat.)

## Fix

Move the overflow containment off the fill wrapper (which must not clip
horizontally) onto `#ask-page` itself:

- `chatFillClass`: drop `max-lg:overflow-hidden`.
- `#ask-page`: `lg:overflow-hidden` → `overflow-hidden` (all breakpoints). The
  panel is the full-viewport-width element, so clipping *its own* content
  (internal scroll containment, rounded card at lg) chops nothing off the sides.

`getBoundingClientRect` can't see this — overflow clips paint, not layout — so
it was verified with screenshots.

## Verification

- `AskView.spec.ts`: overflow assertion moved from the wrapper to the panel;
  suite green (40).
- Rendered the exact DOM chain (`#app-page` → fill wrapper → `#ask-page` →
  transcript) with the compiled CSS at 390px in Playwright: panel spans 0→390px
  (full width). Toggling `overflow-hidden` back onto the wrapper reproduced the
  grey gutters + left-clip from the report; removing it fixed both.
