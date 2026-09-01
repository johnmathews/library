# Sharding the e2e matrix

**Date:** 2026-09-01
**Branch:** `e2e-shard`

## 1. What went

With the backend suite down to ~5 minutes (see
[260901-backend-ci-3x-faster.md](260901-backend-ci-3x-faster.md)), `e2e` is the
pipeline's long pole on its own — 11.3 minutes, and the whole run's wall clock
is now essentially just this job.

Measured from the step timings of run 33477145227:

| step | time |
|---|---|
| Run Playwright tests | **407.4s** |
| Build the stack image (bake) | 111.2s |
| Install Playwright browsers | 82.3s |
| Start the backend stack | 23.1s |
| everything else | ~25s |

The 407s is not one slow thing. It is five browser projects run end to end at
`workers: 1`: tablet-webkit 148.6s, mobile-webkit 144.3s, chromium 84.4s,
firefox 6.0s, webkit 5.7s.

## 2. Why sharding and not `workers: 4`

`workers: 1` is not an oversight. The specs share one backend and assert on
library-wide state — 15 of the 26 spec files click `.first()` of the dashboard
grid, and facet counts are global. Two specs running concurrently against one
stack would see each other's documents, which is the same shape as the
`document_date`-pollutes-the-sort failure this repo has already hit once.

Sharding by **project across parallel jobs**, each with its own stack, keeps
serial execution exactly as it is — a shard still runs its own specs one at a
time — and takes the step down to its longest single project rather than the
sum of five. Nothing about the specs' concurrency assumptions changes.

Three shards, `fail-fast: false` so a red mobile shard still tells you whether
desktop and tablet are green:

| shard | projects | browsers | tests |
|---|---|---|---|
| desktop | chromium, firefox, webkit | chromium firefox webkit | 96.1s |
| mobile | mobile-webkit | webkit | 144.3s |
| tablet | tablet-webkit | webkit | 148.6s |

Selection goes through `E2E_PROJECTS` in `playwright.config.ts` rather than a
`--project` flag, because `test:e2e:ci` chains `assert-e2e-ran.mjs` with `&&`
and `npm run … -- --project=x` would append the flag to the *assertion*, not to
Playwright.

## 3. The duplicate-upload coupling was already handled

`playwright.config.ts` justified `workers: 1` partly with "run them serially so
the later projects deterministically hit the duplicate-upload path". Giving each
shard its own stack removes that ordering: every shard is now "the first
project" and its uploads land as fresh content.

That turns out to be fine, and it is worth being explicit about why rather than
discovering it in CI. The specs never depended on which branch they took —
`library.spec.ts` and `markdown-reader.spec.ts` both assert
`indexed.or(duplicate)`, with a comment saying both are correct outcomes — and
`library.spec.ts`'s detail test already seeds `w11-${project.name}-${Date.now()}`
precisely so it never deletes the shared fixture out from under another
project's re-upload. Separate stacks are strictly more isolated than one shared
one, so the direction of the change is toward less coupling, not more.

## 4. Two smaller things, one of which is a non-fix

**Per-shard browsers.** `mobile-webkit` and `tablet-webkit` are WebKit
descriptors, so downloading and apt-installing Chromium and Firefox for them was
pure waste — and those are the two *long* shards, so it comes straight off the
critical path. The `actions/cache` key gains the shard name, without which a
WebKit-only cache could be handed to the shard that needs all three.

**`--with-deps` stays.** The 82s install step looked like an obvious win: the
browser cache *hits* (474MB restored in 8.5s) and then `npx playwright install
--with-deps` runs a full `apt-get update` and install anyway. But the cached
thing is the browser binaries; the apt dependencies are not cached and every run
gets a fresh VM, so the install is not redundant — it only *looks* redundant
next to a cache hit. Installing fewer browsers is the honest saving; skipping
the deps would have been a green-until-webkit-fails change.

## 5. The did-it-actually-run floor had to be re-derived

`scripts/assert-e2e-ran.mjs` fails a run that reported success without executing
anything, with a floor of 40 executed tests against a real run's 137. Sharding
breaks that number: no single shard executes 137.

Per-project executed counts, taken from the `playwright-json-report` artifact of
run 33477145227 rather than estimated:

```
chromium       executed  51  skipped   5
mobile-webkit  executed  44  skipped  12
tablet-webkit  executed  40  skipped  16
firefox        executed   1  skipped   0
webkit         executed   1  skipped   0
```

So the shards set floors of 30 / 25 / 22 against actuals of 53 / 44 / 40. They
**sum to 77 against the single floor of 40 they replace**, which is the point
worth recording: splitting a gate across jobs usually weakens it, and this one
had to come out stronger or it was not worth doing.

The second hole sharding opens is a mistyped project name: `--project=nope`
would run zero tests, and a job that runs nothing must never look like a job
where nothing was wrong. `selectedProjects()` throws on an unknown name, listing
the known ones. Verified by running `playwright test --list` for each shard
value (58 / 56 / 56 of 170 collected) and with a deliberate typo.

## 6. Measured, and what is left

Two runs on the PR (33494844387, 33496082090). Per-shard job time:

| shard | run 1 | run 2 | tests, run 2 |
|---|---|---|---|
| desktop | 5.8m | 4.7m | 97.4s (53 executed) |
| tablet | 7.1m | 5.6m | 159.6s (40 executed) |
| mobile | 5.2m | 8.8m | 346.6s (45 executed) |

So `e2e` goes from 11.3m to **the longest shard**: 7.1m, then 5.6m — except run
2's mobile shard, which took 8.8m. That one is worth reading carefully, because
the headline number is not the interesting part.

The executed counts came out at exactly the predicted 53 / 45 / 40 against
floors of 30 / 25 / 22, so §5's derivation holds.

Fixed setup is now ~135s per shard — image bake ~80s, stack ~22s, node/npm
~10s, browsers ~30s. The browser step is the one that moved: **82.3s → ~30s**,
and the desktop shard installing all three engines also takes ~30s, which says
the original 82s was mostly a cold cache rather than the extra two browsers.
The 80s bake is now the biggest fixed item, paid three times over.

**The mobile outlier is one flaky spec, not the sharding.**
`facets.spec.ts:41` timed out at the full 180s test timeout and then passed on
retry in 3.3s. Take that one flake out and mobile is ~166s of tests and ~5.0m
of job — in line with the others.

This is pre-existing and unrelated to this change: the same flake would have
burned the same 180s in the old single job, where it was invisible inside a
407s step. But it matters more now. A flake that costs 180s used to be 44% of
one step; it is now the difference between a 5m and a 9m critical path, and it
is the *only* thing between this job and a predictable ~5.5m. Worth its own
investigation, with the caveat that a 3-minute timeout followed by a 3-second
pass is the signature of a wait that has no business being that patient rather
than of a slow machine.

Three ways to attack that were considered and rejected, recorded so they are not
re-litigated from scratch:

- **`needs: build`, pull `ghcr.io/…:${GITHUB_SHA}` instead of baking.** Removes
  three redundant builds but serialises behind `build` (~2.5m), landing around
  7.2m — worse than paying for parallel builds.
- **One job bakes, shards `needs:` it and hit a warm gha cache.** Same problem:
  the serialised bake costs more than the three parallel ones save.
- **Run Playwright from `mcr.microsoft.com/playwright`.** Removes the browser
  install entirely, but the job also drives `docker compose` and a vite preview
  server on the host, so it would need `docker run --network host` around just
  the test step. Plausible, ~60s, and genuinely separable from this change.
