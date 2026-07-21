# Ask mobile — dock the composer above the keyboard (fixed-height chat column)

Date: 2026-07-21

## 1. Problem

Phone feedback after the density pass: the composer floated **mid-screen** on
short conversations (grey empty space below it), and when the keyboard opened the
UI slid up but **not to the right height** — a gap / overlap with the keys.

## 2. Root cause (one cause, two symptoms)

The shell is `#app-shell { height: 100dvh; overflow: hidden }` → `#app-content`
scrolls → `sticky top-0 h-16` header → chat. The composer was `sticky bottom-0`.
**`sticky bottom-0` only pins when the content overflows the scroll area.** A
short chat doesn't overflow, so the composer sat at the natural end of the content
(mid-screen). And because the chat was normal page-flow (not anchored to a
viewport-height column), nothing tied the composer to the *visible* viewport, so
the keyboard landed it at the wrong offset. Same cause. (`confirmed` — reproduced
at 390px; measured the composer's bottom edge at ~520px of an 844px viewport
before the fix.)

## 3. Fix

Make the **mobile chat a fixed-height flex column** instead of page-flow + sticky:

- `AskView.vue`: on the chat screen (`chatFillClass`, gated to `max-lg` +
  `mobileScreen==='chat'`) the root is `flex flex-col h-[calc(100dvh-4rem)]`
  (`-my-8` cancels `#app-page`'s `py-8`); `#ask-page` and the thread pane get
  `max-lg:flex-1 max-lg:min-h-0`; the **transcript** becomes the internal scroll
  area (`max-lg:flex-1 max-lg:overflow-y-auto`, `lg:pb-28` kept for desktop); the
  **composer** drops `sticky` on mobile and is a `shrink-0` **footer**
  (`lg:sticky lg:bottom-0` restores the desktop bar). Bottom padding now includes
  `env(safe-area-inset-bottom)` for the home indicator.
- `index.html`: viewport meta gains `interactive-widget=resizes-content` so the
  keyboard shrinks the `dvh` viewport (Chromium/modern iOS) and the footer sits
  right above the keys.

Desktop is unchanged (page-scroll + `lg:sticky` composer).

## 4. Verification

- Frontend **1029** unit tests pass (+1; updated the composer-dock test to
  footer/`lg:sticky` + a fixed-height-column test). `vue-tsc`/`eslint`/`vite
  build` clean.
- **Structural fix verified in a headless browser at 390px**: on a short chat and
  a new chat, `#ask-form`'s measured bottom edge = **844px = viewport height**
  (was mid-screen). Screenshots confirm the composer docks at the bottom with the
  empty space now above it, inside the scroll area.

## 5. What is deliberately not done / open

- **The exact on-screen-keyboard alignment is iOS-specific and could not be
  reproduced in the headless (desktop-Chromium) harness** — this ships on the
  CSS/`dvh` + `interactive-widget` approach that is the current recommendation,
  and **John is the real-device tester**. If `dvh` alone doesn't nail it on his
  iOS version (there is a known iOS 26 `visualViewport.offsetTop`-doesn't-reset
  quirk), the fallback is a small **VisualViewport-API** listener syncing a CSS
  var to `visualViewport.height` — a deliberate second pass, not baked in now.
  Grade: keyboard-above behaviour is **strongly supported** (matches current
  guidance) but **not device-confirmed** by this session.
- Docs: `docs/ask.md` §1.6 + re-stamp, `docs/frontend.md` AskView row updated.
