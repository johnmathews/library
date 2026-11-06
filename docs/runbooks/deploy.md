# Deploy runbook

**Status:** active. **Last updated:** 2026-11-06 (§1.3 step 5: the deploy prunes old images; §1.6 step 1: find the rollback SHA from git, not the host). Earlier: 2026-11-05 (§1.2: which CI jobs run on which change — docs-only PRs skip e2e, compose-smoke and typecheck; ci-gate's `expect.e2e` rule; job time limits and the apt retries). Earlier: 2026-11-04 (§1.3 and §1.7: compose leftover containers). **Supersedes:** none.
**Last verified:** 2026-11-06 — method: partial, scoped to §1.3's prune step: `tests/test_deploy_script.py` runs `deploy()` end to end against a fake ssh/docker (seen red with the call removed and with `-a`), and the same prune run by hand on the host freed 12GB. 2026-11-05: §1.2's CI paragraphs. Read `ci.yml` and `scripts/ci_gate.sh`; ran `tests/test_ci_gate.py` and `tests/test_ci_workflow.py` (both seen red first), and the retry loop in an ubuntu:24.04 container as a sudo user. The table is from reading the job `if:`s; a live docs-only PR is the next check. §1.3/§1.7's leftover handling was last verified 2026-11-04 (`tests/test_deploy_script.py` against a fake ssh/docker).
**Covers:** scripts/deploy.sh, scripts/ci_gate.sh

How to ship a merged change to the live `documents` LXC. This is the focused
"do it now" version; the full topology and rationale live in
[../deployment.md](../deployment.md) (§1.7.2 for the live instance).

## 1.1 TL;DR

```bash
scripts/deploy.sh
```

Run it from the repo root once `main` is green in CI. It SSHes to the prod host,
pulls the new image, migrates, recreates web + worker, and verifies. Done.

## 1.2 Preconditions

1. **Your change is merged to `main` and CI is fully green.** The `build` and
   `promote` CI jobs must finish — `promote` is what retags
   `ghcr.io/johnmathews/library:latest` to the new build. Deploying before
   `promote` finishes redeploys the *old* image. Check:
   ```bash
   gh run list --branch main --limit 1
   ```
   The script now **enforces this automatically** (the *promote gate*, §1.3): it
   verifies `:latest` resolves to the same digest as `:<HEAD-sha>` and aborts
   if `promote` hasn't caught up yet, so the footgun above is caught before any
   image is pulled.

   **What runs on which change.** The `changes` job sorts the diff into
   `backend` (`src/`, `tests/`, `scripts/`, `models/`, `recall-baseline.json`,
   the build and dependency files), `docs` (`docs/`), `frontend`
   (`frontend/`) and `ci` (`.github/`). On a pull request:

   | Change touches | Runs (besides `changes`, `docs-stamps`, `ci-gate`) |
   | --- | --- |
   | only `journal/` or a top-level `*.md` | nothing else |
   | `docs/`, with no code path | `backend`, `build` (tests read docs) |
   | `backend` paths | `backend`, `typecheck`, `build`, `e2e`, `compose-smoke` |
   | `frontend` paths | `frontend`, `build`, `e2e`, `compose-smoke` |
   | `.github/` | everything |

   On `main` every job runs whatever changed: `promote` needs e2e and
   compose-smoke, and the image bakes `docs/*.md`, so a docs-only merge still
   builds, e2e-tests and promotes.

   **What "green" means.** The run's aggregator job is **`ci-gate`**, which
   passes the `needs.<job>.result` of every other job except `promote` —
   including `docs-stamps` and `typecheck` — to `scripts/ci_gate.sh`. That
   script tolerates `skipped` for the path-filtered jobs (`backend`,
   `frontend`, `e2e`, `compose-smoke`, `build`, `typecheck`) — a skipped
   *required* check would block a merge forever — but requires **`changes`**
   (the path-filter job all the others declare `needs:` on) to be exactly
   `success`, since a broken `changes` skips every one of them and would
   otherwise leave the gate nothing to reject. It also gets
   `expect.e2e=<changes.outputs.e2e>`, the same output e2e's `if:` reads:
   with `expect.e2e=true` a skipped e2e is red ("job 'e2e' was required to
   run for this change"), so an `if:` mistake cannot quietly stop e2e on a
   code PR. A value other than `true`/`false` is red too.
   `tests/test_ci_gate.py` exercises those cases, and
   `tests/test_ci_workflow.py` checks `ci.yml` itself: the gate's `needs:`
   equals its argument list, e2e's `if:` and `expect.e2e` read the one
   output, the filters sort sample paths as in the table above, and every
   job has a `timeout-minutes`.

   **Time limits and apt retries.** Every job has a `timeout-minutes` (5 to 35,
   about 2-4x its normal duration). The runner's apt mirror sometimes stalls
   for many minutes, so the two apt steps, the backend's OCR packages and
   e2e's "Install Playwright system dependencies", try up to 3 times, each
   attempt killed after 300 s. A run that fails all three ends with
   `::error::... failed 3 times`; rerun the failed jobs
   (`gh run rerun <id> --failed`).

   The `main` ruleset **does** require `ci-gate` (it also blocks deletion and
   force-pushes, requires a pull request, and has no bypass actors), so a red
   gate blocks the merge rather than merely reporting it. Note `promote` sits
   *outside* the gate, which is why the promote gate above exists.

   **`typecheck` runs two steps.** `uv run mypy` is green, and — since the
   quarantine was cleared — green because the tree is clean rather than because
   anything is suppressed. `scripts/check_mypy.py` keeps that honest: it re-runs
   mypy with any `[[tool.mypy.overrides]]` `disable_error_code` lifted
   (regenerating the config *from* `pyproject.toml`, so a settings change
   reaches it with no edit) and compares the per-module, per-error-code counts
   against `mypy-baseline.json`, currently `{}`. It fails on a rise, on a fall
   not locked in by lowering the number, and on an override that has stopped
   suppressing anything. Re-quarantining a module is allowed; doing it without a
   measured count beside it is not.
2. **Key-based SSH to the host works** (`ssh documents true` returns instantly,
   no password). The script aborts early if it can't connect non-interactively.
3. **No schema-incompatible change shipped without a backup plan.** The new
   migrations run automatically (step 1.3). They are normally additive and have
   down-migrations, but if a migration is destructive or risky, take a DB backup
   first (see [../deployment.md](../deployment.md) §1.6).

## 1.3 What `scripts/deploy.sh` does

It first probes SSH reachability (`ssh <host> true`), then runs the **promote
gate** locally: it inspects the registry (via `docker buildx imagetools`, no
pull) and confirms `:latest` resolves to the same digest as
`ghcr.io/johnmathews/library:<HEAD-sha>`. If `promote` hasn't retagged this
commit yet — or `docker`/git isn't available to check — it aborts *before
pulling or deploying anything*, so it can't silently redeploy the previous
image.

Then, on the host, in `/srv/apps`, it removes any **compose leftovers** (below)
and runs:

```bash
docker compose up -d --pull always library-migrate library-webserver library-worker
```

If that fails, it removes the leftovers again and retries once. Afterwards it
removes any leftover still present and **aborts if a `library-` container is
`Created`** (created, never started: the stack may be down), naming the
containers.

**Compose leftovers.** To recreate a container, `docker compose up` first
renames the old one to `<12 hex>_<name>`. When that is interrupted the
temporary container stays, and the next `up` fails with "container name ... is
already in use" and leaves every library container `Created`, i.e. production
down. It happened on 2026-10-31 and twice on 2026-11-04 (once a running
duplicate embedder after an otherwise good deploy, once a ~2.5 minute outage).
The script removes only containers of the compose project whose names match
`^[0-9a-f]{12}_library-`; they are compose's temporaries by construction.

Then it verifies:

1. **`--pull always`** fetches the freshly-promoted `:latest`.
2. **`library-migrate`** (one-shot) applies any new Alembic migrations
   transactionally, then exits. The script reads its exit code and **aborts if
   it is non-zero** (web/worker would otherwise run against an un-migrated DB).
3. **`library-webserver` + `library-worker`** are recreated on the new image.
4. **`GET /healthz`** must return OK, and its **body** is then read: a
   missing OCR model weight (`ocr_models: "missing"`) **aborts** — the image
   did not ship `models/ocr/`, and photo OCR would fail one document at a time
   with everything else green (GH #109). Any other `status: "degraded"` is
   reported but not fatal. See deployment.md §1.4.2.
5. **Prunes old images** with `docker image prune -f`, then prints the space
   reclaimed and the root disk's free space. Each deploy leaves the previous
   1.4GB image behind untagged, and the host's root disk is 16G: 14 of them
   had filled it to 97% by 2026-11-06. It removes dangling images only, never
   `-a`, which would also delete tagged images no container is running. A
   failed prune does not fail the deploy, and a deploy that aborts earlier
   prunes nothing.
6. Prints the running images and the prod Alembic head.

Service names on the live host are `library-*` (the repo's compose file uses
`api`/`worker`/`db`; production renames them — see the deployment doc). The
script uses the production names.

## 1.4 Other modes

```bash
scripts/deploy.sh --status   # show the running stack + Alembic head, no deploy
scripts/deploy.sh --logs     # tail recent webserver + worker logs
scripts/deploy.sh --force    # deploy WITHOUT the promote gate (emergencies)
scripts/deploy.sh --help     # usage
```

`--force` (or `SKIP_PROMOTE_CHECK=1`) bypasses the promote gate — use it only
when you've verified `:latest` is current yourself (e.g. `docker`/git isn't
available locally to run the check). You then own not redeploying the old image.

Overrides (env): `LIBRARY_DEPLOY_HOST` (default `documents`),
`LIBRARY_DEPLOY_DIR` (default `/srv/apps`), `SKIP_PROMOTE_CHECK` (`1` = bypass
the promote gate, like `--force`).

## 1.5 Verify after deploy

The script already checks migrate + `/healthz`, but for a human sanity pass:

```bash
scripts/deploy.sh --status        # web/worker/db/embedder up, head = expected revision
ssh documents 'cd /srv/apps && docker compose logs --tail 30 library-worker'
```

Then click through the changed surface in the browser.

## 1.6 Rollback

The image is content-addressed by commit SHA, so rollback = redeploy the
previous SHA:

1. Find the previous good commit on `main` (`git log --first-parent main`).
   CI pushes an image tagged with its full SHA to ghcr, so the tag is
   `ghcr.io/johnmathews/library:<full-sha>`. The host does not keep old
   images: every deploy prunes them (§1.3), and they were untagged locally
   anyway, so `docker images` on the host cannot show their SHA.
2. Pin the stack to the previous `:sha` tag (edit `/srv/apps/.env` or the compose
   image ref — back it up first) and `docker compose up -d`. See
   [../deployment.md](../deployment.md) §1.7.
3. **Migrations do not auto-roll-back.** If the bad deploy ran a migration that
   the old image can't tolerate, downgrade it explicitly
   (`docker compose run --rm library-migrate alembic downgrade -1`) or restore
   the pre-deploy DB backup (§1.6 of the deployment doc).

## 1.7 Troubleshooting

1. **`Cannot SSH to 'documents'`** — `ssh documents true` fails. Fix your SSH
   config/keys, or set `LIBRARY_DEPLOY_HOST`.
2. **`library-migrate exited <n>`** — a migration failed; the script aborts
   before declaring success. Read `docker compose logs library-migrate`, fix
   forward or roll back. The DB is left at whatever revision the failed
   transaction reached (migrations are transactional, so a failed step rolls
   itself back).
3. **`These containers were created but never started`** — the retry did not
   clear compose's name conflict, and the stack may be down. Run
   `ssh documents 'docker ps -a --filter name=library'`; confirm any
   `<12 hex>_library-…` container is compose's (`docker inspect` labels
   `com.docker.compose.project=apps`, `State.StartedAt` 0001-01-01),
   `docker rm` it, and re-run `scripts/deploy.sh`.
4. **`/healthz` not OK** — `docker compose logs library-webserver`. Common
   causes: bad env in `/srv/apps/.env`, db not healthy yet (re-run after a few
   seconds), or a startup exception in new code.
