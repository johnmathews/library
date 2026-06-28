# Ask: kill the spurious "Something went wrong" alert on a successful ask

**Date:** 2026-06-28

## 1. Symptom

In `frontend/e2e/ask-page-citation.spec.ts` a **successful** (mocked) ask
rendered the answer turn + citation correctly, but the page *also* showed a red
`AppErrorSummary`: "There is a problem → Something went wrong — try again." The
e2e didn't assert on the alert, so it stayed green and the bug was latent —
but a successful answer must never show an error.

## 2. Root cause (confirmed, not guessed)

Followed `systematic-debugging`. Wrote a deterministic Vitest repro first
(mock `askQuestion` to resolve with a response **omitting `thread_id`**, submit,
assert no error-summary) — it failed exactly as predicted: the turn rendered
(length 1) **and** the error-summary appeared.

Chain, in `AskView.vue` `onSubmit`:

1. The e2e mock fulfilled `POST /api/ask` with a body that **omitted
   `thread_id`** (`{ answer, citations, used_tools, cost_usd }`). The real
   backend (`src/library/api/ask.py`) and the `AskResponse` type always include
   it, so the mock was incomplete versus the contract.
2. On `/ask` (no route param) `onSubmit` called
   `router.replace({ name: 'ask-thread', params: { threadId: res.thread_id } })`
   with `res.thread_id === undefined`.
3. Vue Router **rejects** that navigation ("Missing required param
   'threadId'"). The rejection was caught by `onSubmit`'s answer-error `catch`,
   which ran `friendlyError(error)` → a non-`ApiError` throw maps to the generic
   "Something went wrong — try again." The answer had already rendered, hence
   *success + error*.

## 3. Fix

The answer renders **before** any of the post-success bookkeeping. That
bookkeeping (record `thread_id`, sync the URL, refresh the sidebar) is a side
effect and must never turn a valid answer into an error. Extracted it into a
self-contained `syncThread(thread_id, wasNewThread)` that runs *outside* the
answer-error `catch` and:

- **skips** the URL sync when `thread_id` is missing / non-numeric (defends the
  source — a malformed response can't crash the success path), and
- wraps `router.replace` in its own `try/catch` that **logs** a navigation
  rejection instead of surfacing it (defence in depth — any redundant/blocked
  navigation is non-fatal).

Net: a successful answer can never show an error alert, regardless of response
shape or navigation outcome.

## 4. Tests / docs

- New Vitest regression tests in `AskView.spec.ts`: (a) a successful response
  that **omits `thread_id`** renders the turn with **no** error-summary; (b) a
  normal success **syncs the URL to `/ask/:threadId`** and shows no error
  (locks the happy path the fix must preserve).
- Corrected the e2e mock to include `thread_id`, and added an
  `expect(error-summary).toHaveCount(0)` assertion after a successful ask so the
  regression can't return silently.
- Gate green: `vue-tsc --build`, `eslint .`, `vitest run` (436 passed).
- Updated the `AskView` row in `docs/frontend.md`.

## 5. Notes

- Local e2e repro is blocked on Apple Silicon (the `embedder` image is
  amd64-only); CI (linux/amd64) is the authoritative e2e run. The Vitest repro
  was the fast, deterministic confirmation.
