# Hardening the CI gates

**Date:** 2026-09-01
**Branch:** `ci-gate-hardening`

## 1. What went

Four changes to what CI actually enforces, after an audit prompted by the two
speed PRs earlier today ([backend](260901-backend-ci-3x-faster.md),
[e2e](260901-e2e-sharding.md)). Three are in `.github/workflows/ci.yml`; one is
a repository setting with no representation in the repo at all, which is part
of the point.

The frontend gates themselves needed nothing. `npm ci` → `lint` (ESLint) →
`type-check` (vue-tsc) → `test:coverage` (Vitest, 85%/75%) → `build` →
`check:assets` (the govuk-residue gate) covers every script in
`frontend/package.json` that is a gate. The problems were all in the gating
structure around the jobs, not in the jobs.

## 2. The audit started from a wrong premise, twice

Worth recording because both errors were in the direction of *believing the
repo about itself*.

**The file said branch protection was not configured.** `ci-gate` carried a
`TODO (repo owner): branch protection is NOT configured yet — point it at THIS
job as the sole required check`. Reading that, the obvious conclusion is that
nothing is enforced and a red CI cannot block anything. The GitHub API says
otherwise: the `main` ruleset has required `ci-gate`, and only `ci-gate`, for
some time. The TODO had been done and not crossed off.

A stale TODO is worse than no TODO. It does not merely fail to inform, it
actively misinforms, and it survives exactly because the thing it describes
works — nobody goes back to a comment about a job that keeps passing.

**And `promote` is not skipped for the reason it looks like.** On both PRs
earlier today `promote` showed as skipped, which reads like a consequence of
`frontend` being path-gated (it is in `promote`'s `needs`). It is not:
`promote` is `if: github.ref == 'refs/heads/main'`, and a PR's ref is
`refs/pull/N/merge`. It is skipped on every PR by construction. The observation
that the deploy gate had not been exercised until the work reached main was
right; the mechanism was not.

## 3. Required checks were not strict — and that is today's bug

`strict_required_status_checks_policy` was `false`. A PR could merge with checks
that had passed against a base that no longer existed.

That is not hypothetical here. It is what happened this morning: #143's checks
were green against a base without #142, GitHub reported `MERGEABLE` / `CLEAN`
throughout, and the auto-merged `journal/README.md` failed
`build_journal_index.py --check`. It was caught by dry-running the merge
locally, which is not a gate — it is a habit, and habits do not survive being
in a hurry.

Now `true`: a branch must be up to date with main before it can merge. The cost
is a re-run per merge, which is affordable precisely because CI went from 16–23
minutes to ~9 earlier today. The speed work is what makes the stricter gate
practical, which is a nicer relationship between the two than it first looks.

## 4. Two `ci-gate` check-runs per SHA, disagreeing about e2e

`on: push: branches: ["**"]` plus `pull_request` meant every push to a branch
with an open PR produced **two** workflow runs. Both emit a check-run named
`ci-gate` against the same commit — and they do not verify the same thing: the
push run deliberately skips `e2e`, the PR run does not.

So the required check on a SHA reflected whichever run finished last. Two
`ci-gate` rows against one commit were visible in `gh pr checks 142` the whole
time and read as harmless duplication.

`branches: [main]` now. The PR run is the gate — it is what the ruleset
requires and the only one that runs e2e. The push run's only unique
contribution was feedback before a PR exists, which `workflow_dispatch` and
opening the PR both cover. It also halves the CI minutes on every branch push.

## 5. A duplicated gate cost six minutes on every journal commit

The `backend` path filter included `docs/**`, `journal/**` and `**/*.md`, so a
journal-only change — this repo's most frequent commit, 178 of them — ran the
full backend suite. Both of today's earlier PRs did exactly that.

What appeared to justify it was `test_every_citation_in_the_repo_resolves`. It
did not. The test walks `check_docs.gated_documents()` applying
`check_work_unit_citations` to each — which is what `check_docs.py` itself does
over the same set, in the unconditional 20-second `docs-stamps` job. A second
copy of a live gate, and the copy that mattered was the other one.

Its name was also a considerable overstatement. "Every citation in the repo"
meant "every citation in the 21 stamped documents". Demonstrated both ways: a
bogus `(W99)` in `docs/observability.md` is caught by `check_docs.py` alone
(exit 1, `unknown-work-unit`); the same citation in a journal entry is caught by
**neither** the test nor the gate. The test had never covered the paths the
filter was pulling in on its behalf.

So: test deleted, and `journal/**` and `**/*.md` dropped from the filter.
`docs/**` **stays** — the backend suite genuinely reads four real documents via
`check_docs.py`'s `WORK_UNIT_PLAN`, `MODULE_MAP_DOC` and `DOCS_INDEX` constants,
all under `docs/`, so a docs change can legitimately red it. Nothing under
`tests/` reads `journal/` or any `.md` outside `docs/`; that was checked by
grepping every non-monkeypatched `REPO_ROOT` use rather than assumed.

Coverage was re-proved after the deletion, not before: the bogus-citation
experiment still exits 1.

## 6. What is still open

**The `changes` filters are themselves untested.** A new top-level directory
claimed by no filter would skip every unit job, and `ci-gate` counts a skipped
job as a pass — by design, because a skipped *required* check blocks a merge
forever. The design is right; the missing piece is a test asserting that every
tracked top-level path is claimed by at least one filter. That is the same shape
as the hole `assert-e2e-ran.mjs` exists to close, and it has no guard today.

Deliberately not done here: it wants a real think about what "claimed" means for
paths like `.gitignore` or `LICENSE` that legitimately gate nothing, and this
change was already four unrelated things.
