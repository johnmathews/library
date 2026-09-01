# Ask: document mode becomes the default, and the collapsed rail's actions come back

**Date:** 2026-08-21

Follow-up to [260821-ask-document-view.md](260821-ask-document-view.md), same day.

## What prompted it

The `/done` wrap-up on the first change ran a code-review pass over the merged
diff and found a gap I had introduced and shipped:

> Document mode hides the conversation rail. The rail is the only place at `lg+`
> that carries a "New conversation" button (`ConversationSidebar.vue`, the
> `max-lg:hidden` one) — the mobile `＋` is `lg:hidden`. So with a thread open in
> document mode there was **no way to start a new conversation** without first
> toggling back to conversation mode.

At that point it was a one-extra-click annoyance behind an opt-in mode. Then the
decision was made to have **document be the default at `lg+`**, which turned the
same gap into the *default desktop experience* — and also into "no way to switch
to a different conversation", since the thread list lives in the same rail.

## Why the tests didn't catch it

This is the part worth keeping. The first change shipped with a test that did
exactly this:

```ts
await w.get('[data-testid="ask-view-mode-document"]').trigger('click')
expect(w.find('[data-testid="conversation-sidebar"]').exists()).toBe(false)
```

That is an assertion about the **mechanism** — the rail disappeared, as designed.
It is not an assertion about the **capability** — that the things the rail
carried can still be done. Both the unit spec and the e2e spec were written that
way, so both were green while the feature had a hole in it. `confirmed`: reverting
the fix in this change turns three tests red, one of which is the *pre-existing*
`disables New conversation in the fresh state` test — meaning that once document
became the default, CI would have caught it. It did not catch it before, because
with conversation as the default the rail was always present in that test.

Generalised into `docs/frontend-view-principles.md` §4: if a wide-only mode hides
a container, relocate what the container held, and assert the capability by a
selector that doesn't care which container hosts it.

## What changed

1. **`useAskViewMode` defaults to `'document'`** instead of `'conversation'`.
   The clamp is untouched, so a phone still renders bubbles — and, importantly,
   still does so *without* writing the fallback into storage.
2. **The rail's actions relocate to the thread bar while the rail is collapsed**
   (`isLargeScreen && !showConversationRail`, so the two sets are mutually
   exclusive by construction):
   - **＋ New** — deliberately reuses the rail button's `new-conversation`
     testid, so the capability stays addressable by one selector wherever it
     currently lives. Carries the same `newConversationRedundant` disabled rule.
   - **conversations** (`ask-show-conversations`) — routes to `/ask`, which sets
     `hasChatContext` false and therefore brings the rail back, so a different
     thread can be picked.

## Tests

Rewrote the specs that encoded the old default rather than loosening them — a
default flip should make those tests *say something different*, not say less:

- The composable spec now seeds the **non-default** (`conversation`) wherever it
  is checking that stored values are honoured, shared, and persisted. Seeding
  `document` would now pass even if storage were ignored entirely.
- Added a test that the default preference is `document` *and* the effective
  render is `conversation` on a narrow screen — both halves, because a clamp
  that wrote the fallback back to storage would pass a one-sided check.
- Added four capability tests: New reachable while collapsed, a route back to
  the list, never both control sets at once, and the relocated buttons absent
  from a phone's DOM.

1087 unit tests green (was 1081). Coverage 90.35 / 82.86 / 89.24 / 92.73 against
the 85/85/85/75 floor.

## What is deliberately not done

1. **The thread list itself is not surfaced in document mode.** Switching
   conversations is two clicks (conversations → pick), not one. A popover thread
   picker in the thread bar would fix that, but it duplicates the rail's list UI
   and its rename/delete affordances — real scope, and the two-click path is
   coherent rather than broken.
2. **The rail collapse is still automatic, not user-controllable.** A "pin the
   rail" toggle would let someone have document typography *and* the list, at
   the cost of the 288px the mode exists to reclaim. Deferred until someone
   actually wants it.
3. **No migration for existing stored preferences.** Anyone who explicitly chose
   `conversation` keeps it; anyone with nothing stored gets the new default.
   That is the intended behaviour, but it does mean users who toggled to
   `conversation` during the few hours PR #81 was live will not see the new
   default. Not worth a migration.
4. **No visual verification of the running app.** Same limitation as the first
   change: the layout is asserted by e2e rect measurements against a live stack,
   not by anyone looking at it.
