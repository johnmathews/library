# Measuring layout, and running a journey that never ran

**Date:** 2026-08-12. **Units:** W22, W23 (batch L of the library-defect-generators run).

Two frontend units. W22 gives the Smart Groups journey somewhere to actually
execute; W23 adds geometry specs for the three components with the worst fix
history. Both are about the same thing: a test that never runs and a test that
cannot fail are the same test.

## 1. The composer spec had to be shown failing, and nearly wasn't

The acceptance criterion for W23 was explicit — `ask-layout.spec.ts` must fail
when the `bf8da0c` composer fix is reverted, because a layout spec never shown
to fail is decoration. Getting there turned up the more interesting result.

The obvious spec opens `/ask/new`, measures the composer and asserts it sits at
the bottom of the viewport. Measured on the real stack:

| | composer bottom | gap below |
|---|---|---|
| with the fix | 687 | **33px** |
| `bf8da0c` reverted | 711 | **9px** |

The reverted layout puts the composer *closer* to the bottom. A spec written the
obvious way would have passed against the bug — and passed more comfortably than
against the fix.

The reason is in the commit message, which describes the defect as appearing
"after a short transcript": the unbounded panel grows to match the taller
conversation-list sidebar, and the composer lands mid-panel. With no transcript
there is nothing to grow. So the spec stubs `POST /api/ask`, asks one question,
and only then measures. In that state the numbers separate cleanly:

```
ask composer: bottom edge is 189px above the viewport bottom (max 40px).
Rect {"x":577,"y":420,...,"bottom":531}, viewport 720px.
```

33px with the fix, 189px without. The 40px tolerance is the measured gap plus
rounding, not a round number chosen to make the test pass. Two of the five tests
fail on the revert.

## 2. Two facts about this app that specs keep tripping over

Both cost real time and are now in `e2e/fixtures/layout.ts` rather than in
someone's memory.

**`window.scrollTo` does nothing.** The shell is a fixed-height flex column and
`#app-content` is the element that scrolls, so `document.documentElement`
reports `scrollHeight === clientHeight` on every page, always. Scrolling the
window and then waiting for something scroll-triggered produces a 15-second
timeout and "element(s) not found", which reads like a layout regression rather
than a spec bug. `scrollAppContentToBottom` is three lines and a long comment.

**The action dock is not in the DOM until you scroll.** It is `v-if`-mounted by
an IntersectionObserver on `#document-hero` — deliberately, so it cannot
interfere with mobile specs. And the shared e2e fixture document renders exactly
one viewport tall, so there is nothing to scroll and the dock never appears at
all. `detail-layout.spec.ts` therefore seeds its own 120-paragraph note first.

The intermediate step I did not take: forcing the dock into existence by
shrinking the viewport to 300px tall, which does work. It would have been
asserting geometry in a configuration no device has.

## 3. A 422 that documents an API sharp edge

`PUT /api/settings/appearance` with `{dock_position}` alone is a 422:
`background_tone` is the one field on `AppearancePreferences` with no default.
And every *other* field does have one, so a partial PUT silently resets them.
The spec now sends the whole payload and says why, which is the honest form —
the alternative was a spec that mysteriously needed an unrelated key.

## 4. The nightly is a heartbeat, not a gate

`smart-groups.spec.ts` has always been gated behind `E2E_SMART_GROUPS`, and its
own rationale for that is sound: the journey needs the async OCR → chunk → embed
pipeline to have indexed two fresh documents *and* the semantic sweep to score
them as a match. Neither is deterministic inside a merge gate. There is also a
harder reason — the CI `e2e` job starts `db migrate api worker` and no embedder
at all, so the spec cannot pass there by construction.

The defect was never the gate. It was that `E2E_SMART_GROUPS` was set **nowhere
in the repo**, so the gate always fired and the journey had never executed
anywhere, ever. The spec was a description of a test, not a test.

`e2e-nightly.yml` brings up the full stack including the embedder, waits for
TEI's `/health` **from inside the compose network** (the embedder publishes no
ports — curling `localhost:8080` from the runner would have waited out the full
timeout and then failed, having proved nothing), and runs the one spec on
chromium. Not in `ci-gate`, not a `promote` gate: a nightly failure is a signal
to look at, and a flaky merge blocker gets disabled within a week and then
protects nothing.

It reuses `assert-e2e-ran.mjs`, whose floor of 40 assumes the full matrix. That
is now overridable via `E2E_MIN_EXPECTED`, and the override is a floor rather
than an off switch: a value that is not a positive integer exits **2** rather
than falling back to something permissive. A gate whose disable path is a typo
is not a gate.

**This unit's acceptance criterion is not yet met.** It requires
`gh workflow run e2e-nightly.yml` to show `1 passed` — and a `workflow_dispatch`
workflow cannot be dispatched until it exists on the default branch. It also
cannot be checked locally: TEI publishes no arm64 image, so there is no embedder
on this machine at all. The dispatch happens after merge, and the result stands
on its own.

## 5. What ran

The full Playwright matrix, against a real stack: **110 passed, 21 skipped**, up
from 105 before. The stack ran on port 8100 beside the developer's own library
stack on 8000 — which is why `vite.config.ts` grew a
`LIBRARY_API_PROXY_TARGET` override. Checking whether `vite preview` needed its
own `proxy` block turned out to be worth doing empirically: it does not, it
inherits `server.proxy`. The first version of that comment said the opposite,
and the check that disproved it had to compare the `/healthz` **body** — the SPA
fallback returns 200 for an unproxied path, so a status-only check passes either
way and proves nothing.
