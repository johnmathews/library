# Ask, laid out like a chat app

**Date:** 2026-10-22

## 1. What & why

The user found `/ask` cramped: "a window within a window within a window". At
1440px the answer sat inside five frames. There was the app shell, then a page
header with a lede, then a bordered card, then a thread bar inside the card,
then a bordered answer card inside the transcript. The composer was a strip at
the card's bottom. The brief was to make it work like the Claude desktop app in
layout, positioning and one-click features, not in colour.

## 2. Decisions

The user chose three things up front:

- **No persistent conversation list.** Conversations switch from the title's ▾
  (search plus every conversation, refetched on each open), so the reading
  column keeps the width. The 288px rail and the phone's separate list screen
  are gone, and `ConversationSidebar.vue` was deleted with them.
- **App header plus one slim thread bar**, rather than putting the thread title
  in the shared `AppHeader`.
- **All four one-click actions:** copy, retry, edit, and a model and backend
  chip under the composer.

Decisions made while building, with the reason for each:

- **One centred `max-w-3xl` column shared by the answers and the composer.**
  The first cut put the transcript's side padding inside the column and the
  composer's outside it, which made the box 48px wider than the text. Moving
  the padding outside made the edges identical. An e2e assertion pins this, and
  it fails by exactly 24px when the padding goes back inside.
- **Conversation is the default layout under a new storage key.**
  `library:ask-view-mode` defaulted to document view because a bubble beside the
  rail wasted width, and the rail is gone. `useStorage` had already written
  `document` into every browser that had opened Ask, so changing the default
  alone would have changed nothing for those users. The key is now `-v2`, and
  `frontend-view-principles.md` §4 records the rule.
- **Retry and edit replace turns on the server only after the new answer
  succeeds.** `POST /api/ask` takes `replace_from_turn_id`, cuts history before
  that turn, and deletes it and every later turn in the commit that adds the new
  one. A retry that hits an expired login, which happened earlier the same day,
  therefore costs nothing. The client shows the replacement as pending and puts
  the old turns back on failure. Both halves are mutation-checked.
- **`AskRequest` now refuses unknown fields.** The first backend test of the
  new field reached the real Anthropic client, because the unknown field had
  been silently ignored and the request answered as a plain follow-up.
- **The citations label sits in its own span.** `ask-page-citation.spec.ts`
  finds it by an anchored `/^Citations \(\d+\)$/`, and a flex `<summary>` with
  whitespace around the text never matched. The old `AppDetails` met that
  contract by accident; the new markup meets it on purpose.

## 3. Verification

- **Backend:** 75 Ask and LLM-backend tests, ruff and mypy are clean.
- **Frontend:** 1456 unit tests, eslint and vue-tsc are clean.
- **e2e:** the three Ask specs pass on chromium, mobile-webkit and tablet-webkit
  against a local stack built from the branch.
- **In a browser:** the layout was looked at in Chromium at 1440px (dark and
  light) and 375px, with a seeded three-turn conversation containing a table,
  a list and citations.
- **Not exercised:** a live answer from a real model. The stack had no Claude
  credentials, so every answer in these checks is seeded or stubbed.
