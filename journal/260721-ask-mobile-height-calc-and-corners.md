# Ask mobile — fix the chat-height calc (iOS) and corner clipping

Date: 2026-07-21

## 1. Problem (two phone bugs, reported with screenshots)

1. The mobile chat **didn't fill to the bottom** on iOS — a grey gap below the
   composer, and the composer's position still depended on content length (the
   pre-"fixed-height column" behaviour), i.e. the previous fix wasn't taking
   effect **on the device** even though it verified fine in the headless browser.
2. The composer's **Send / paperclip corners were clipped** by the phone's
   rounded screen corners — too close to the edge.

## 2. Root cause of #1 (confirmed)

The fixed-height class was `h-[calc(100dvh-4rem)]` — **no whitespace around the
`-`**. CSS `calc()` requires whitespace around binary `+`/`-`, so
`calc(100dvh-4rem)` is **invalid** and the declaration is dropped. Verified in a
browser:

- `CSS.supports('height', 'calc(100dvh-4rem)')` → **false**
- `CSS.supports('height', 'calc(100dvh - 4rem)')` → **true**

Chromium happened to give the column a height of 780px anyway via the flex
fallback (which is why my earlier headless check measured the composer at the
viewport bottom), but **iOS Safari rejects the invalid rule outright**, leaving
the column with no height → content-height → the composer floats. A test-browser
pass masked a device-only failure — the class was never valid CSS.

## 3. Fixes (`AskView.vue`)

1. **Valid calc via Tailwind underscores:** `h-[calc(100dvh-4rem)]` →
   `h-[calc(100dvh_-_4rem)]` (underscores render as spaces →
   `calc(100dvh - 4rem)`). Re-verified: `CSS.supports(... 'calc(100dvh - 4rem)')`
   → true; the column now has an explicit height instead of relying on a
   Chromium-only fallback.
2. **Corner clearance:** the composer footer gained side padding (`px-2` →
   `px-3`, `sm:px-4`) so the pill and its Send/paperclip sit further from the
   curved left/right edges, and the bottom padding was bumped to
   `calc(env(safe-area-inset-bottom) + 0.625rem)` to keep the buttons above the
   bottom corner arc / home indicator.

## 4. Verification

- `CSS.supports` check flipped false→true; the chat root computes an explicit
  `780px` at 390×844 with the composer's bottom edge = viewport height.
- Frontend **1029** unit tests pass; `vue-tsc`/`eslint`/`vite build` clean.

## 5. What is deliberately not done / open

- **Still device-blind on two iOS specifics:** whether `100dvh - 4rem` exactly
  fills below the header on the real device (safe-area-top isn't handled anywhere
  — header is a bare `h-16`), and whether the new corner insets are enough for
  the actual screen-corner radius. Both are John's real-device checks. If the fill
  is off by the status-bar/safe-area, the follow-up is to subtract
  `env(safe-area-inset-top)` too or move the fill into the shell's flex chain; if
  corners still clip, increase the inset. The **calc-validity bug is confirmed and
  fixed** regardless.
- The keyboard-above behaviour (VisualViewport fallback) remains the reserved
  next step if `dvh` alone doesn't nail it now that the height is valid.

## 6. Lesson

A CSS class that "works in the test browser" can still be **invalid CSS** that a
stricter engine drops. For arbitrary Tailwind values, `CSS.supports()` the
generated declaration — don't infer validity from one engine's forgiving
fallback.
