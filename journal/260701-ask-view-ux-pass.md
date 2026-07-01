# Ask view UX pass — disabled New-conversation, scroll affordance, answer surface

Date: 2026-07-01. A focused engineering-team cycle on the `/ask` chat surface,
driven by a four-item user request. Item 1 (cross-browser per-user history) was
dropped after confirming it already works — `AskThread.user_id` scoping with real
cookie/token auth and a server-side thread list. The other three shipped.

## 1. What changed

### 1.1 Item 2 — "New conversation" disabled in the fresh state

`AskView.vue` now computes `newConversationRedundant` and passes it to
`ConversationSidebar` as `:new-disabled`. The sidebar button gains a real
`disabled` attribute + greyed styling and a guarded `@click` when set.

### 1.2 Item 3 — visible scroll affordance

The transcript and the sidebar thread list used `.no-scrollbar`, which hid the
scrollbar entirely and removed the affordance that the region scrolls
independently. Added a `.thin-scrollbar` utility (subtle thin bar, transparent
track, dark-mode aware) in `utility-patterns.css` and swapped both regions to it.
Left `AppSidebar.vue`'s `.no-scrollbar` alone (deliberate app chrome).

### 1.3 Item 4 — answer on a subtle surface card

The agent answer was plain markdown painted directly on the panel background.
Wrapped the answer region (pending indicator + resolved answer + citations +
meta) in a single bordered, lightly-shaded card (`bg-gray-50
dark:bg-gray-900/40`), giving three legible layers: panel → violet question
bubble → gray answer card. Also changed the citation link hover from
`hover:bg-gray-50` to `hover:bg-white` (the card is now gray-50, so a gray-50
hover would be invisible).

## 2. Key decisions

### 2.1 The disable condition is composer-visibility aware, not just "fresh"

The naive condition (`threadId === null && turns.length === 0`) broke the mobile
composer-reveal flow (W12): on a phone the composer is hidden until "New
conversation" reveals it, so disabling the button in the fresh state would strand
the user with no way to open the composer. The shipped condition is
`fresh && (isLargeScreen || composerOpen)` — redundant only when the composer is
already on screen (desktop always; mobile once opened). `isLargeScreen` uses
`useMediaQuery('(min-width: 1024px)')` from `@vueuse/core` (already a dependency;
the codebase already reads the same breakpoint in `AppSidebar.vue`).

### 2.2 e2e updated to match the new desktop behavior

`e2e/ask-page-citation.spec.ts` unconditionally clicked "New conversation" from
the fresh `/ask` state. On the `chromium` project (≥1024px) that button is now
disabled, so Playwright's actionability check would hang. The spec now clicks it
only when the composer is not already visible (`#ask-question` hidden), after
waiting for the sidebar button to confirm the view mounted. This mirrors the real
UX and is correct across the three projects that run the spec (chromium +
mobile/tablet webkit).

## 3. Docs

Updated `docs/ask.md` (Web UI section) and `docs/frontend.md` (utility list +
`AskView` reference cell) for all three items. While auditing `frontend.md` I also
corrected two pre-existing stale claims from earlier runs: the question is a
right-aligned violet bubble (not an `<h2>`), and the Send button becomes a live
Stop button while answering (not "Sending…"). Historical `docs/superpowers/plans`
and `specs` were left as point-in-time records.

## 4. Tests

- `ConversationSidebar.spec.ts`: +1 test — the button carries `disabled` and does
  not emit `new` when `newDisabled`.
- `AskView.spec.ts`: +2 tests — New conversation disabled in the fresh (desktop)
  state and re-enabled after an ask (stubs `matchMedia` for the lg viewport;
  `vi.unstubAllGlobals()` in `afterEach` keeps the mobile default for other
  tests); the answer renders on a `data-testid="ask-answer-surface"` card. Also
  extended the transcript-scroll test to assert `thin-scrollbar` present /
  `no-scrollbar` absent.
- Full frontend suite: 513 pass. Coverage: 89.38% statements overall, AskView.vue
  95.65% lines. Lint + type-check clean.

## 5. Notes

A dedicated code-review subagent traced every reachable state of the disable
condition, all three e2e projects, and dark-mode contrast of the new surface and
scrollbar — verdict clean. The visual result (scrollbar + surface + greyed button)
is best confirmed on the deployed preview, since a real screenshot needs the
authenticated full stack; the exact classes are locked by unit tests.
