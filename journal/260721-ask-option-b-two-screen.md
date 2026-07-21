# Ask UI redesign — two-screen, route-driven chat (Option B)

Date: 2026-07-21

## 1. Problem

On a phone, `/ask` landed on a **list-management** screen: page header + "New
conversation" + a "Search conversations" box + a thread list with always-on
Rename/Delete links — and **no visible composer** (it was hidden behind a
`composerOpen` reveal, patched in [[260630-ask-mobile-composer-reveal]]). That is
the opposite of every mainstream chat app. The 2026-06-30 patch treated the
symptom (dead "New conversation" button) but kept the root cause: the
conversation rail owned the whole first screen.

Run via the engineering-team skill (evaluate → mockup → plan → build). The
evaluation report and plan live in `.engineering-team/runs/manual-20260721T093000Z/`.

## 2. What shipped — Option B

Ask is now **two full-screen routes on mobile**, driven by the route name so the
phone's back gesture and browser history behave like a native chat app:

| Route | Name | Mobile | Desktop (`lg+`) |
| --- | --- | --- | --- |
| `/ask` | `ask` | conversation **list** | two-pane, none selected |
| `/ask/new` | `ask-new` | fresh **chat** (empty state) | two-pane, composer focused |
| `/ask/:threadId(\d+)` | `ask-thread` | **chat** for that thread | two-pane, active |

- `router/index.ts`: added `/ask/new` (before the param route); constrained
  `:threadId` to `(\d+)` so `new` is never parsed as an id.
- `AskView.vue` rewritten: mobile screen = `route.name` (`mobileScreen`
  computed); the rail and the thread column are each `max-lg:hidden` on the
  other's screen. **Deleted the `composerOpen` reveal hack** — the composer is a
  `sticky bottom-0` footer, always present on the chat screen; the transcript
  carries `pb-28` so its last citations never sit under the composer (the
  historical overlap bug that made the old design avoid sticky). Added a chat
  **title bar** (back arrow on mobile, thread title, ⋯ menu), a **new-chat
  greeting + example prompts** that fill the composer, and an **inline paperclip**
  attach replacing the labelled "Attach image" button. The `PageHeader` is now
  desktop-only (`max-lg:hidden`); mobile gets a compact "Ask" + ＋ title bar.
- `ConversationSidebar.vue`: the always-on Rename/Delete links moved into a new
  **`ThreadActionsMenu.vue`** (a ⋯ overflow menu). The two-step delete and inline
  rename are unchanged; the menu just gates them.
- `/ask?q=…` deep links (the document-detail "Ask about this document" button)
  are redirected to `/ask/new?q=…` so the seed lands on the chat screen where the
  composer lives — the external button needs no change.

## 3. Key decision — route-driven, not local state

The mobile screen is derived from `route.name`, **not** a local `mobileScreen`
ref. Rejected local state because it breaks the hardware/gesture back button and
deep-linking. Cost: one extra route and a digit constraint on the param. This is
what makes back/forward and "share this conversation" behave correctly.
(`confirmed` — verified in a browser: list → thread → back → list, and
`/ask/new` → send → URL becomes `/ask/:id`.)

## 4. Bugs found in code review (both fixed)

An independent review pass caught two real bugs the passing suite missed:

1. **(Critical) Stale in-flight answer corrupts the newly-active thread.**
   `onSubmit` captured `pendingIndex` but wrote the result to `turns.value` read
   at *resolution* time. Switching threads mid-request (very reachable —
   `@select` and the mobile ＋ aren't disabled while answering) replaces
   `turns.value`, so the answer landed in the wrong thread's array (corruption,
   or an `undefined` deref → render crash) and `syncThread` could force-navigate
   back. Fix: capture the transcript array reference at send time and bail in
   both the success and error branches if `turns.value` changed; `applyRoute`
   also aborts an in-flight ask on navigation. Regression test added.
2. **(Important) Two ⋯ menus open at once.** The trigger's `@click.stop` stops
   the click before it reaches the bubble-phase document listener, so opening
   menu B never closed menu A. Fix: register the click-outside listener in the
   **capture** phase (`addEventListener('click', fn, true)`).

## 5. Tests & verification

- Unit: `AskView.spec.ts` gained the two-screen model, greeting/prompts, back
  arrow, title-bar rename/delete, and the stale-answer regression; removed the
  two obsolete `composerOpen` reveal tests and the "composer not sticky"
  assertion. `ConversationSidebar.spec.ts` opens the ⋯ menu before
  rename/delete. Full frontend suite **1025 passed** (+1); `vue-tsc`, `eslint`,
  `vite build` clean.
- e2e `ask-page-citation.spec.ts`: the mobile path now goes to `/ask/new`
  (composer present on every viewport) instead of clicking "New conversation" to
  reveal it; the citation deep-link assertions are unchanged. Self-skips locally;
  runs on chromium + mobile-webkit + tablet-webkit in CI.
- **Visual:** drove the real dev build in a headless browser (auth + ask API
  stubbed via `addInitScript`) at 390px and 1280px — list, existing chat, new
  chat, and desktop two-pane all render as intended.

## 6. Docs

- `docs/ask.md` §1.6 "Web UI" rewritten for the two-screen model + re-stamped.
- `docs/frontend.md` `AskView` reference row rewritten (routes, ⋯ menu, pinned
  composer, inline paperclip, greeting).

## 7. What is deliberately not done

- **No backend/API change.** `api/ask.ts` and the endpoints are untouched.
- **No streaming, no answer-rendering change** — markdown/citations/cost/thinking
  indicator preserved, only re-homed.
- **No app-nav change** — the redesign reuses the existing shell; the earlier
  idea of routing the conversation list through the app's own `AppSidebar`
  drawer (Option A) was not taken, since the user chose Option B (dedicated
  routes).
- **Composer is `sticky`, not a fixed viewport-height chat pane.** A true
  fixed-height flex chat column would pin the composer even for short
  conversations, but needs viewport/header-height math that risked the overlap
  bug; `sticky bottom-0` + transcript padding was the lower-risk choice. Worth
  revisiting if the composer scrolling with very short chats feels off.
