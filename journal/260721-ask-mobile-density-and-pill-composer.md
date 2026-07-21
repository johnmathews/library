# Ask mobile — full-bleed chat + full-width pill composer

Date: 2026-07-21

## 1. Problem

Phone feedback on the just-shipped Option B Ask UI: "boxes in tiles within
tiles… all the padding reduces usable space… the text entry should be full
width… buttons and options should not be inline because the width is so small."
Screenshots confirmed it: the chat sat in a bordered white card inside the padded
page, and the composer wedged a `.form-textarea` boxed field (~50% width) between
a paperclip and Send, under a two-line "Enter to send…" hint.

Run via engineering-team (evaluate → mockup → plan → build). Artifacts in
`.engineering-team/runs/manual-20260721T120000Z/`. Mockup approved before build.

## 2. What shipped (`AskView.vue` only — plus specs/docs)

- **P1 — Full-bleed on mobile.** `#ask-page` drops its border/rounding/shadow and
  breaks out of the shell's side padding (`-mx-4 sm:-mx-6 lg:mx-0`) so the chat
  runs edge-to-edge; `-mt-4 lg:mt-0` tightens the top. Transcript padding trimmed
  (`px-3 pt-4 … sm:px-6`), keeping the `pb-28` clearance for the pinned composer.
  All `lg:`-restored to the bordered card on desktop.
- **P2 — Composer is one pill.** Replaced the `AppTextarea` boxed field + inline
  paperclip/Send with a single rounded pill: a **borderless, full-width,
  auto-growing** raw `<textarea>` on top, and **attach + Send/Stop on their own
  row inside the pill**. `autoGrow()` (bound to `watch(question)`) grows it to
  content up to 160px. The two-line hint is gone.
- **P3 — Mobile Enter = newline.** `onComposerKeydown` now branches on
  `isLargeScreen` (`useMediaQuery('(min-width: 1024px)')`): Cmd/Ctrl+Enter always
  sends, Shift+Enter/Ctrl+J always newline; plain Enter sends at `lg+` but
  inserts a newline below `lg`, where the Send button is the way to send.
- **P4 — De-nested turns.** The shaded, bordered answer surface card is now
  `lg:`-gated; on mobile the answer is flat text under the violet question bubble.

The pill composer applies at all widths (a deliberate exception to "mobile only" —
it reads well on desktop too and keeps one composer). Desktop otherwise unchanged:
two-pane card, rail, answer card all preserved.

## 3. Decisions

- **Route/logic untouched.** Only layout/density + the one keyboard-behaviour
  change. Optimistic send, Stop/abort, images, citations, the two-screen routing,
  and the stale-answer guard from the prior run are all as-is.
- **Break out with negative margins**, not by editing `DefaultLayout`'s shared
  `#app-page` padding — so no other view is affected. (`confirmed` — desktop and
  other routes unchanged; verified visually.)
- **Pill everywhere, card `lg:`-only.** The composer box is the one nested tile
  worth keeping as a single pill on all widths; the answer card only earns its
  border where there's width for it (desktop).

## 4. Verification

- Frontend **1028** unit tests pass (+3: full-bleed classes P1, pill textarea P2,
  mobile-Enter P3; plus the item-4 test regeared to `lg:`-gated card). `vue-tsc`,
  `eslint`, `vite build` clean.
- **Visual** (real dev build, stubbed API): 390px new-chat and chat — full-bleed,
  flat answer, full-width pill, a follow-up wrapping across full-width lines, no
  hint; 1280px desktop — carded two-pane preserved, pill composer.
- Independent code review found no bugs above threshold.

## 5. Docs

- `docs/ask.md` §1.6 updated (full-bleed, pill composer, mobile Enter, flat turns)
  + re-stamped. `docs/frontend.md` AskView row's composer/turn description updated.

## 6. What is deliberately not done

- **`autoGrow` doesn't recompute on device rotation / breakpoint resize** — a
  multi-line draft keeps its cached height until the next keystroke (cosmetic,
  self-heals on the next input). A `resize`/`orientationchange` listener would fix
  it; skipped as low-value for now. (Flagged by review, below its threshold.)
- **No backend/API change; no streaming.** Density and one keyboard tweak only.
