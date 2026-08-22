# Page title in the app bar

**Date:** 2026-08-22

## 1. The band nobody was using

Every authenticated page had two horizontal bands at the top. The `h-16` navbar,
whose **entire left half was empty** at `lg+` (hamburger only, and that is
`lg:hidden`). And below it, inside `#app-page`, a `PageHeader` spending ~44px on
an `<h1>` that mostly restated the sidebar item already highlighted three inches
to its left.

That is affordable on a scrolling list. It is not affordable on `/ask`, which is
a fixed-height flex column sized off the remaining viewport — every pixel the
header took came straight out of the transcript.

## 2. What moved

The title now renders in `AppHeader`, beside the hamburger. This is the standard
contextual top-app-bar pattern (Material's top app bar; Linear, Notion, Vercel),
so it costs no novelty budget.

The *value* stays the view's. `PageHeader` still takes a `title` prop; it just
claims it for the bar through a new `usePageTitle` singleton instead of rendering
it. The page still has exactly one `<h1>`, at the top of the window rather than
the top of the body — so `getByRole('heading', { name: 'Documents' })`, which a
dozen e2e specs use as their post-sign-in gate, kept working untouched.

**Why a singleton and not the two obvious alternatives.** Route `meta.title` was
rejected because it duplicates a value the view already owns and cannot express a
dynamic title. `<Teleport>` was rejected for two concrete reasons: the target's
existence becomes a mounting-order dependency, and per-breakpoint visibility
classes on the `PageHeader` root (`AskView` has `max-lg:hidden`) would stop
applying to the node once it left that subtree — the title would have silently
reappeared on mobile.

**Claims are token-owned.** `releasePageTitle(token)` is a no-op unless that
token still owns the title. Vue happens to unmount the outgoing view before
mounting the incoming one, so without the guard this would work — right up until
it didn't, and then the bar would blank on every navigation. The guard is pinned
by a test confirmed to red when it is removed.

## 3. What stayed in the body, and how it is styled

The one-line description stays at the top of `#app-page`. With no title above it
any more, a full-width dark paragraph would read as body copy, so it is styled as
a **lede**: muted, `text-sm`, and capped at `max-w-2xl` (~70 characters) rather
than running the shell's full 96rem. Actions keep their right-aligned slot, and
because `justify-between` parks a *lone* child on the left, the actions div
gained `sm:ml-auto`.

The important half: **a `PageHeader` given only a title now renders nothing at
all.** Documents, Settings, Admin, Recently Deleted and Saved views pass exactly
that, and leaving an empty `mb-6` band above their content would have reproduced
the wasted band this whole change set out to remove. Documents in particular now
opens with its search bar directly under the app bar.

## 4. Two follow-on tidies that fell out

`AskView`'s mobile list screen had its own big `<h1>Ask</h1>` next to a ＋
button. With the bar showing "Ask" on every breakpoint that was a second visible
title *and* a second `<h1>`; the heading is gone and the ＋ (the only way to
start a chat on a phone — the rail's "New conversation" is `max-lg:hidden`)
right-aligns on its own.

`docs/frontend.md`'s accessibility section already recorded making the sidebar
wordmark a `<p>` so the heading list didn't lead with "LIBRARY" instead of the
page title. That note now says where the page title went.

## 5. Measured, not asserted

Real stack again — compose + `vite preview` + Playwright — with screenshots of
Documents, Ask, Upload, Charts, Settings and New note at 1440×900, plus Upload
and the Ask list at 375px. The Ask panel's top edge moved from y=190 to y=145:
**45px reclaimed**, read off the two shots rather than estimated.

**1099** unit tests and the full e2e suite (**123 executed**, all passing).

One honest note on that e2e run: it first came back with six failures in
`admin-views.spec.ts`, which was **my missing local fixture** — I had created the
`e2e` user but not `e2e-admin` — not a regression. Creating the admin user turned
all nine admin tests green. Recorded because "six tests failed and I decided it
was fine" is exactly the shape of claim that deserves the receipt.

## 6. Known nit, not fixed

`AppHeader`'s inner container has no `max-w-*`, while `#app-page` caps at
`max-w-[96rem]`. Below ~1792px of viewport they share a left edge and the title
lines up with the page content; above it, the bar keeps going and the title drifts
left of the content. Left alone deliberately: `ActionDock`'s documented
positioning depends on the navbar spanning the full width, so this is a change to
make on purpose, not as a side effect of moving a title.
