# Ask mobile: hide composer by default, make "New conversation" reveal it

Date: 2026-06-30

## 1. Problem

On the `/ask` page on mobile the layout stacks sidebar → transcript → composer,
so the "Your question" box sat far below the fold — you had to scroll past the
whole sidebar and an empty transcript to find it. And the prominent violet "New
conversation" button only cleared state (off-screen, since you're scrolled to
the top), so it appeared to "do nothing".

## 2. Change (`AskView.vue`)

- Added `composerOpen` ref (default false). The composer `<form>` gets
  `:class="{ 'max-lg:hidden': !composerOpen }"` — **hidden on mobile until
  opened; always docked at lg+** (the gate is a no-op there).
- `openComposer()` sets the flag, focuses `#ask-question`, and
  `scrollIntoView`s the form. Wired into `resetConversation()` so **"New
  conversation" now reveals + focuses the composer** (on desktop too — it
  focuses the box, so the button no longer feels inert).
- `loadThread()` sets `composerOpen = true` (without stealing focus) so opening
  a thread — via the sidebar or a deep link — exposes the follow-up composer.
- Empty-state copy now points at the button ("tap 'New conversation' to ask
  one") instead of a non-existent "below" composer; kept the asserted
  "Select a conversation from the sidebar," prefix.

Kept the composer inline (not `position: sticky`/`fixed`) to avoid
reintroducing the citation-click overlap the docked-composer comment warns
about.

## 3. Tests

- Unit: composer carries `max-lg:hidden` by default; clicking `new-conversation`
  removes it and focuses `#ask-question`; opening a thread reveals it.
- e2e `ask-page-citation.spec.ts`: the composer is now collapsed by default on
  the mobile/tablet projects, so added a `new-conversation` click before
  filling `#ask-question` (a no-op for visibility at lg+). Same class of fix as
  the markdown-reader/notes mobile-collapse change — see
  [[library-frontend-responsive-e2e]].

## 4. Verification

Frontend **510 passed** (+2); eslint, vue-tsc, vite build clean. No backend
change. e2e self-skips locally; the new-conversation open step is the only
change to that path.

## 5. Post-mortem — why "+ New conversation" was useless (desktop included)

**Original behaviour** (`resetConversation`): `turns=[]`, `threadId=null`,
`question=''`, `errorMessage=null`, `router.push({name:'ask'})`. Nothing else.

**User-confirmed symptom:** it *does* work when a conversation is already
selected (clicking clears it and the textarea becomes the way to start the new
one) — but does nothing from the empty state. That **state-dependent
inconsistency** is the confusing part, and exactly what the fix removes.

**Root cause — a no-op in the most common state.** The button is most naturally
clicked when you want to start fresh — but if you are *already* on a fresh
`/ask` (the default landing state, and the state right after a previous "new
conversation"), every assignment is a no-op (already empty) and
`router.push({name:'ask'})` navigates to the route you're already on (a
redundant navigation vue-router silently rejects). Net observable change: zero.
It only "did something" from the narrow path of *viewing a thread* → click →
transcript clears. There was no focus, scroll, transition, or toast to signal
"you can type now". On desktop the composer was already on screen, so even a
real reset gave only a subtle transcript-clear; from the empty state, nothing.
On mobile the composer was below the fold, so even that was invisible.

**Why it shipped / wasn't caught:**
- It was scoped as "wire the button to reset state + navigate" — which the code
  does correctly. The missing piece was the *affordance* (focus/scroll), which
  was never in the spec.
- Tests asserted the **mechanism**, not the **user-observable outcome**:
  `ConversationSidebar.spec` checks the button **emits `new`**; nothing asserted
  that anything *perceivable* happens from the empty-state path. A green suite
  coexisted with a dead button because the tests verified "event fired / state
  reset", not "the user can tell something happened".
- e2e never clicked the button from the empty state (the ask e2e goes straight
  to typing).
- Review mentally simulates the working path (thread → reset), not the
  degenerate path (already-empty → reset, all no-ops).

**Fix:** `resetConversation` now calls `openComposer()` → focuses
`#ask-question` and scrolls it into view (and reveals it on mobile). Clicking
the button always produces observable feedback now, regardless of start state or
viewport.

**Prevention (recorded in memory):** test the user-observable *outcome* of a
control, not just that it emits/resets; manually exercise primary buttons from
their most common starting state (here, the empty state).
