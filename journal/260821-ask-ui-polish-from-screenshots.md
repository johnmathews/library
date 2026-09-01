# Ask: two fixes that only a screenshot could find

**Date:** 2026-08-21

Follow-up to the document-view work shipped earlier today. Both fixes came from
the user sending screenshots of the running app — the first time anyone had
actually *looked* at it. Every prior entry today carried the same caveat: "no
visual verification of the running app". This is what that caveat was worth.

## 1. Two adjacent buttons meaning different things, styled identically

The thread bar, when the rail is collapsed, carries `⋯` (thread actions), `≡`
(conversations), `＋` (new conversation), then the layout toggle pair.

The toggle uses a violet fill to mean **"this mode is active"**. The `＋` button
was given `bg-violet-500` too — so a *verb* and a *state* sat side by side
looking like one control group, two violet squares in a row.

Nothing was broken; every test passed; the DOM was correct. It simply read
wrong, and reading wrong is not something the test suite has an opinion about.

`＋` is now a ghost button matching `≡`. Rule for this bar: **violet fill means
active; actions are ghosts.**

## 2. Opening a conversation landed you mid-answer

`loadThread` called `scrollToBottom`. For a chat where a message just arrived
that is right — you want the new thing. For *opening* a thread to read it, it
lands the transcript partway down the answer with the question you asked
scrolled off the top. The question is the first thing needed to make sense of
what follows.

Document mode sharpens this: the whole point of that layout is reading.

`loadThread` now scrolls to the top. New answers still pull to the bottom.

## The test that proved nothing

Worth recording, because it is the failure mode this session kept warning about
and then walked straight into.

The first version of the scroll test asserted `transcript.scrollTop === 0` after
opening a thread. It passed. It also passed **with the fix reverted** — so it
tested nothing.

The reason: jsdom has no layout, so `scrollHeight` and `clientHeight` are both
`0`. `scrollToBottom`'s overflow branch (`scrollHeight > clientHeight + 1`) is
therefore false, it falls through to `scrollIntoView`, which jsdom no-ops, and
`scrollTop` stays `0` whichever function ran.

Fixed by giving the element real geometry via `Object.defineProperty` before the
load resolves, so the two behaviours actually diverge. `confirmed`: it now fails
when the fix is reverted.

The general shape — **a test whose assertion is satisfied by the environment
rather than by the code** — is invisible unless you revert the fix and watch.
Nothing else catches it.

## Verification

- 2 new tests; both confirmed to fail with their fix reverted.
- Frontend suite 1089 passed (was 1087). Coverage 90.38 / 82.87 / 89.25 / 92.77
  against the 85/85/85/75 floor.
- `eslint`, `vue-tsc --build --force`, `vite build`, `check_docs` clean.

## What is deliberately not done

1. **Still no visual regression testing.** Both defects were found by a human
   looking at a screenshot. Nothing in CI renders the page and compares it. That
   remains true after this change.
2. **The layout toggle is unreachable from the empty state.** It lives in the
   thread bar, which only renders when a conversation is open — so with nothing
   selected there is no way to see or change the layout. Defensible (the setting
   has nothing to act on yet) but worth naming.
3. **No change to the scroll behaviour on `ask-new`.** A fresh chat has nothing
   to scroll; left alone.
