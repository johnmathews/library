# Ask composer — one flat bar instead of a pill in a box

**Date:** 2026-08-22

## 1. The complaint

On desktop the `/ask` composer read as *a box within a box*. Two surfaces were
stacked with nothing between them:

1. The `<form id="ask-form">` — a footer strip carrying `border-t
   border-gray-200`, `bg-white dark:bg-gray-800` and `lg:rounded-b-xl`. Its top
   divider, closed off left/right/bottom by the panel card's own border, drew a
   rectangle whose entire contents were —
2. the composer pill: `rounded-3xl border border-gray-300 bg-gray-50
   dark:bg-gray-900/40`.

The form's background was *literally the same colour* as `#ask-page` behind it,
so the outer rectangle contributed one divider line and nothing else. It existed
only to hold the pill.

## 2. What changed

The form **is** the text-entry surface now. There is no nested field.

- The form keeps `border-t` and `shrink-0` and gains the fill (`bg-gray-100
  dark:bg-gray-900/40`) plus the `focus-within:border-violet-400` that used to
  live on the pill. `lg:rounded-b-xl` stays — the panel clips anyway, but it
  keeps the fill correct if that ever changes.
- The pill wrapper collapses to a bare `<div :class="contentWidthClass">`, which
  draws nothing. It survives only to centre the field on the transcript's measure
  in document mode, where `contentWidthClass` is non-empty.
- Gutters moved to the form (`px-3 sm:px-6`, matching the transcript's own) and
  the textarea's horizontal padding went to zero, so the placeholder now lines up
  with the answer text above it rather than sitting 12px further in.
- The attach button's hover was `hover:bg-gray-100` — the new surface colour, so
  it would have hovered to invisible in light mode. Bumped to `gray-200`.

## 3. Two things deliberately kept

**The top rule.** With `focus:outline-none focus:ring-0` on the textarea, that
border is the composer's **only visible focus indicator**. Removing it as
"superfluous chrome" would have been a WCAG 2.4.7 regression, which is not what
"fewer elements" was asking for.

**A fill, not a fill-plus-border, as the surface marker.** `gray-100` on white is
~1.06:1, well under the 3:1 that WCAG 1.4.11 wants of a field boundary — the
first draft of this change (recorded here because it was wrong for a reason worth
keeping) kept a rounded darker pill with a 1px border for exactly that reason.
The flat bar sidesteps the problem instead of losing to it: the bar is not a
free-floating control needing its own outline, it is a region of the panel, and
its top rule plus the fill change together mark the edge.

## 4. Verifying a visual claim visually

A CSS change asserted through class names is asserting the fix, not the property.
jsdom has no layout, so `AskView.spec.ts` could not have told the difference
between this and the old markup.

So it was checked in the **real stack** — `docker compose up db migrate api
worker`, `npm run build-only`, `vite preview` on :4173, Playwright driving a real
Chromium — and screenshotted at 1440px (light and dark, empty and answered, and
focused to confirm the violet rule) and at 375px. `e2e/ask-layout.spec.ts`'s
existing geometry regressions passed unchanged across chromium / mobile-webkit /
tablet-webkit: **26 passed**, plus **1089** frontend unit tests.

One spec name went stale and was corrected rather than left to rot: *"the send
and attach controls clear the composer pill corners"* → *"…stay inset from the
composer edges"*. There are no pill corners left to clear, but the property that
fix was really about — controls sitting inside the gutters, not flush to the edge
— still holds, and the test still holds it.

## 5. Docs touched

`docs/ask.md` §1.6 and `docs/frontend.md` §1.5 both described "a single
full-width pill"; both now describe the bar, the focus-indicator role of the top
rule, and the zero-padding/gutter alignment. Both status stamps record a
**partial** re-verification scoped to those paragraphs, with the previous
verification carried forward rather than overwritten.
