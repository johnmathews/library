# CI time limits, apt retries, and no e2e on docs-only changes

> Purpose
>
> Records why the e2e install step hung, what PR #211 changed in CI, and
> how each change was proven, including live. Work unit W1 of the MCP
> follow-ups run. This entry ships as its own docs-only PR, which is the
> live proof that such a PR skips e2e.

**Date:** 2026-11-06
**Branch:** `eng-mcp-followups` (PR #211), `eng-mcp-followups-ci-journal` (this entry)

## 1. The hang was apt, not Playwright

The step "Install Playwright browsers" (`npx playwright install
--with-deps`) was cancelled by hand 8 times across 6 runs on 2026-11-04/05,
each after 631–1600 s.

- **Where it stopped:** every hung log stops inside apt, at
  `Get:NN http://azure.archive.ubuntu.com/...`, with single packages taking
  up to 25 s.
- **What was not the cause:** the browser binaries come from the
  `ms-playwright` cache, which hit on every hung job.
- **Normal timing:** median 31 s, p90 61 s, over 97 successful installs.
- **Reruns:** 2 of 7 reruns hung again, so the old note that "a rerun
  passed every time" was wrong.
- **The backend job:** its OCR apt step showed the same slowdowns (615–804
  s) without being cancelled yet.
- **Time limits:** nothing in `ci.yml` had one, so each hang would have run
  to GitHub's 6-hour default.

## 2. What changed

- **The install is two steps.**
  - System dependencies: three attempts of `sudo env "PATH=$PATH" timeout
    --kill-after=30 300 npx playwright install-deps`, with
    `dpkg --configure -a` between attempts and a 16-minute step limit.
  - Browsers: from the cache, with a 5-minute limit.
  - The OCR apt step uses the same wrapper.
- **`timeout` runs inside `sudo`.** A container test showed that an
  unprivileged `timeout` outside `sudo` leaves the root process running and
  holding the apt lock against the retry.
- **Every job has a time limit**, sized from medians over 24 runs and noted
  beside each.
- **The `changes` job has a `docs` filter.**
  - `docs/**` moved out of `backend`, so the backend suite still runs for
    docs (tests read them), but e2e, compose-smoke and typecheck do not.
  - `models/**` and `recall-baseline.json` joined `backend`. Before, a
    weights-only PR skipped the compose-smoke checksum that guards them.
- **One output decides e2e.** `changes.outputs.e2e` is forced on `main`,
  because `promote` needs e2e and the image bakes `docs/*.md`. The e2e
  job's `if:` reads only that output.
- **`ci-gate` knows when e2e was required.** It passes
  `expect.e2e=<that output>`, and `ci_gate.sh` then requires `success`. A
  plain `skipped` result cannot tell a docs-only skip from an `if:` typo on
  a code PR; the expectation can.

## 3. Proof

- **`tests/test_ci_gate.py`:** the expect cases were red against the old
  script, which read `expect.e2e` as a job whose result was `true`.
- **`tests/test_ci_workflow.py` parses `ci.yml`.** It checks:
  - that the gate's `needs:` matches its arguments;
  - that e2e and the gate read the same decision;
  - how 12 paths are classified, including the `frontend/e2e` Markdown
    fixture and `frontend/README.md`, which must count as frontend;
  - that every job has a time limit.

  It was red on the old file, and red under five hand-made mutants.
- **The retry in a container:** in `ubuntu:24.04`, run as a non-root sudo
  user, a 4-second limit killed each attempt mid-download with no apt
  process left behind. With 300 s, the real `install-deps` finished in 33 s.
- **Live, code PR (#211):**
  - All three e2e shards ran.
  - The gate logged `e2e=success expect.e2e=true`.
  - The retry fired on its first live run: the desktop shard's attempt 1
    was killed at 300 s on a slow mirror, and attempt 2 fetched 102 MB in
    59 s.
- **Live, docs-only PR (this one):** expected to show e2e, compose-smoke
  and typecheck skipped, backend and docs-stamps run, and the gate green
  with `expect.e2e=false`. The result is recorded in the follow-up entry
  for the run.

## 4. Left as is

- `build` still runs on a docs-only PR (about 3 minutes, no push); its
  `if:` follows `backend`.
- The retry loop appears twice in `ci.yml`. A shared script would be
  cleaner but was not worth a new file.
- `actionlint` runs with shellcheck disabled, so the retry shell is not
  linted. It was checked by hand.
