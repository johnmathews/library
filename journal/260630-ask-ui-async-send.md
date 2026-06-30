# Ask UI — asynchronous send, selection border, delete confirm

Date: 2026-06-30

## 1. Context

User flagged two `/ask` UI defects from a screenshot plus a general quality
pass. Ran the engineering-team cycle scoped to the Ask surface
(`AskView.vue`, `ConversationSidebar.vue`). Run dir:
`.engineering-team/runs/manual-20260630T121217Z/`.

## 2. Findings (evaluation-report.md)

- **Blocking send (high, reported):** Send button `:disabled="loading"` →
  greyed/inert for the whole LLM call; the user's question didn't appear until
  the answer resolved. No "sent" vs "thinking" distinction.
- **AbortController never abortable (high):** `onSubmit` built a controller and
  passed its signal but never stored it, so cancellation was impossible — dead
  plumbing that became the Stop button's hook.
- **Partial selection border (medium, reported):** active row used
  `border-l-2` + the list's `divide-y`, reading as a left bar + bottom line.
- **Delete without confirmation (medium):** one click on a low-contrast link
  permanently deleted a thread.
- Plus: auto-scroll wouldn't follow a resolving answer (length-keyed watcher),
  no Cmd/Ctrl+Enter, no `aria-live`.

## 3. Changes

- **W1** `ConversationSidebar.vue`: dropped `divide-y`, gave rows `rounded-lg`
  + `ring-1`; active row `ring-violet-500` (full perimeter), inactive
  `ring-transparent`.
- **W2** `AskView.vue`: optimistic turn pushed on submit (`pending` flag),
  composer cleared instantly; thinking-dot indicator in the answer slot
  (`data-testid="ask-thinking"`, `aria-live`/`aria-busy`); hoisted the
  AbortController to a module-scoped `inFlight` so a **Stop** button (warning
  variant, same `ask-submit` testid) cancels it; `AbortError` is silent, other
  errors restore the question + images; `scrollToBottom()` called on push and on
  resolution; Cmd/Ctrl+Enter submit; collapsed the redundant `askQuestion`
  image branch; `loading` → `isAnswering`.
- **W3** `ConversationSidebar.vue`: inline two-step delete via a `confirmingId`
  ref (Delete → Confirm/Cancel).
- Docs: `docs/ask.md` Web UI section updated.

## 4. Verification

- Frontend unit suite: **507 passed** (new tests assert optimistic turn,
  thinking indicator, Stop-aborts-and-removes-turn, error-restores-question,
  Cmd/Ctrl+Enter, full-ring selection, two-step delete).
- `eslint .`, `vue-tsc --build`, and `vite build` all clean.
- `e2e/ask-page-citation.spec.ts` self-skips without `E2E_BASE_URL` (needs the
  live compose stack); the citation/submit path is unchanged (idle button still
  submits).

## 5. Follow-ups

- None blocking. A future enhancement could keep a failed turn visible with a
  "Retry" affordance instead of removing it and restoring the composer.
