# Deploy runbook

**Status:** active. **Last updated:** 2026-11-07 (§1.3: verify waits for the host lock before touching containers, and a failed DB read reports instead of exiting silently; `--deploy` runs the migration with `compose run --no-deps`; §1.6: the rollback downgrade uses `--no-deps` too). Earlier: 2026-11-06 (WUD deploys every new `:latest`; the script verifies by default; §1.2 apt retries; §1.3 image prune). **Supersedes:** none.
**Last verified:** 2026-11-07 — method: partial. §1.3: `tests/test_deploy_script.py` against a fake ssh/docker/flock (seen red), the default verify run against production after WUD deployed `045b235` (passed: HEAD live, `alembic_version` = image head, no stray), and WUD's own log of that deploy, which showed `compose run` without `--no-deps` recreating `library-db`. The rest carries its earlier verifications forward.
**Covers:** scripts/deploy.sh, scripts/ci_gate.sh, scripts/ci_apt_retry.sh

How to ship a merged change to the live `documents` LXC. This is the focused
"do it now" version; the full topology and rationale live in
[../deployment.md](../deployment.md) (§1.7.2 for the live instance).

## 1.1 TL;DR

```bash
scripts/deploy.sh
```

**What's Up Docker (WUD) on the host deploys every new `:latest` by itself,**
about a minute after CI's `promote` job: it pulls, migrates and restarts web +
worker. So after a merge, run the script from the repo root at the merged
commit: it waits until `/healthz` reports that commit, then verifies the
migration, the health detail and the containers, and prunes old images. It
deploys nothing. Only when WUD is paused or broken, deploy by hand with
`scripts/deploy.sh --deploy` (§1.4).

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
   e2e's "Install Playwright system dependencies", both run through
   `scripts/ci_apt_retry.sh`: up to 3 attempts, each killed after 300 s, with
   `dpkg --configure -a` after a failure. Retrying the same slow mirror did
   not help (all three attempts failed on PR #213 and on main `7dfb945`), so
   before each retry the script rewrites the runner's `/etc/apt/apt-mirrors.txt`
   (which apt reads through `mirror+file:`) to put `archive.ubuntu.com` first
   and drop `azure.archive.ubuntu.com`; apt keeps the .debs already fetched.
   A run that fails all three ends with `::error::... failed 3 times`; rerun
   the failed jobs (`gh run rerun <id> --failed`). The notice
   `switched apt mirror to archive.ubuntu.com` in a job log means the first
   attempt was slow. `tests/test_ci_apt_retry.py` covers the script and
   `tests/test_ci_workflow.py` checks both steps call it.

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

**By default it verifies WUD's deploy and changes nothing:**

1. **Waits for HEAD to be live:** polls `/healthz` (inside the webserver
   container) until its `git_sha` is HEAD, for up to 10 minutes
   (`LIBRARY_VERIFY_TIMEOUT`). On timeout it says so and names `--deploy`:
   WUD may be paused, or its deploy failed (`ssh documents 'docker logs --tail
   50 wud'`).
2. **Waits for the host lock** (and releases it at once). HEAD on `/healthz`
   means WUD migrated and started the webserver, not that its script is done:
   it may still be recreating the worker, and compose's in-flight
   `<12 hex>_library-…` rename looks like a leftover. WUD holds the lock until
   its health wait ends.
3. **Compose leftovers** (below): removes any, and **aborts if a `library-`
   container is `Created`** (created, never started: the stack may be down).
4. **Migration state:** the database's `alembic_version` must equal
   `alembic heads` from the running image, or it **aborts**. This holds
   whoever ran the migration, so no container's exit code is involved.
5. **Health detail:** the `/healthz` body. A missing OCR model weight
   (`ocr_models: "missing"`) **aborts**: the image did not ship `models/ocr/`,
   and photo OCR would fail one document at a time with everything else green
   (GH #109). Any other `status: "degraded"` is reported, not fatal. See
   deployment.md §1.4.2.
6. **Prunes old images** with `docker image prune -f`, then prints the space
   reclaimed and the root disk's free space. Each deploy leaves the previous
   1.4GB image behind untagged, and the host's root disk is 16G: 14 of them
   had filled it to 97% by 2026-11-06. Dangling images only, never `-a`, which
   would also delete tagged images no container is running. A failed prune
   does not fail the run, and a run that aborts earlier prunes nothing.
7. Prints the running images and the prod Alembic head.

**Why it no longer deploys by default.** Until 2026-11-06 the script ran its own
`docker compose up` while WUD ran another for the same new image. Both started a
migrate job: on the 0041 deploy one applied the migration and the other failed
with "expected to match one row ... 0 found" (harmless only because the DDL is
transactional). WUD's log shows the collision on both deploys that day.

**`--deploy`, the manual deploy**, runs these steps on the host as one script
under a **host lock**, `flock` on `/srv/apps` itself; WUD's deploy script takes
the same lock, so the two can never overlap (it waits up to 15 minutes for a
running WUD deploy): remove compose leftovers; `docker compose pull`; **run the
migration once** with `docker compose run --rm --no-deps -T library-migrate` (`--no-deps`, or `run` may recreate the running `library-db`), aborting
before web and worker if it fails; then `docker compose up -d --no-deps
library-webserver library-worker` (on failure: remove leftovers, retry once).
Then the seven steps above.

**Compose leftovers.** To recreate a container, `docker compose up` first
renames the old one to `<12 hex>_<name>`. When that is interrupted the
temporary container stays, and the next `up` fails with "container name ... is
already in use" and leaves every library container `Created`, i.e. production
down. It happened on 2026-10-31 and twice on 2026-11-04 (once a running
duplicate embedder after an otherwise good deploy, once a ~2.5 minute outage).
The script removes only containers of the compose project whose names match
`^[0-9a-f]{12}_library-`; they are compose's temporaries by construction.

Service names on the live host are `library-*` (the repo's compose file uses
`api`/`worker`/`db`; production renames them — see the deployment doc). The
script uses the production names.

## 1.4 Other modes

```bash
scripts/deploy.sh --deploy   # deploy :latest by hand (WUD paused or broken), then verify
scripts/deploy.sh --force    # --deploy WITHOUT the promote gate (emergencies)
scripts/deploy.sh --status   # show the running stack + Alembic head, no deploy
scripts/deploy.sh --logs     # tail recent webserver + worker logs
scripts/deploy.sh --help     # usage
```

`--force` (or `SKIP_PROMOTE_CHECK=1` with `--deploy`) bypasses the promote
gate — use it only when you've verified `:latest` is current yourself (e.g.
`docker`/git isn't available locally to run the check). You then own not
redeploying the old image, and the script waits only for `/healthz` to answer,
not for HEAD's commit.

Overrides (env): `LIBRARY_DEPLOY_HOST` (default `documents`),
`LIBRARY_DEPLOY_DIR` (default `/srv/apps`), `LIBRARY_VERIFY_TIMEOUT` (default
600 s), `LIBRARY_VERIFY_INTERVAL` (default 10 s), `SKIP_PROMOTE_CHECK` (`1` =
bypass the promote gate).

## 1.5 Verify after deploy

The script already checks the migration state and `/healthz`, but for a human sanity pass:

```bash
scripts/deploy.sh --status        # web/worker/db/embedder up, head = expected revision
ssh documents 'cd /srv/apps && docker compose logs --tail 30 library-worker'
```

Then click through the changed surface in the browser.

## 1.6 Rollback

The image is content-addressed by commit SHA, so rollback = redeploy the
previous SHA:

0. **Pause WUD first** (`ssh documents 'docker stop wud'`), or it redeploys
   `:latest` within a minute. Start it again (`docker start wud`) once the fix
   or the revert has shipped as the new `:latest`.
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
   (`docker compose run --rm --no-deps library-migrate alembic downgrade -1`) or restore
   the pre-deploy DB backup (§1.6 of the deployment doc).

## 1.7 Troubleshooting

1. **`Cannot SSH to 'documents'`** — `ssh documents true` fails. Fix your SSH
   config/keys, or set `LIBRARY_DEPLOY_HOST`.
2. **`The database is at revision 'X' but the running image expects 'Y'`** or
   **`library-migrate failed — migrations FAILED`** — a migration failed or did
   not run. After a WUD deploy read `docker logs --tail 50 wud`; after
   `--deploy` the failing run's output is in the script's own output (the run
   container is removed). Fix forward or roll back. The DB is left at whatever
   revision the failed transaction reached (migrations are transactional, so a
   failed step rolls itself back).
   - **`Could not take the deploy lock`** (`--deploy`): a WUD deploy held the
     lock for 15 minutes. Check `docker logs --tail 50 wud`; usually just run
     the default (verify) instead.
   - **`/healthz did not report <sha>`** (verify): WUD has not deployed HEAD.
     It may be paused (`docker ps --filter name=wud`), its deploy may have
     failed (its log; a failure also sends a Pushover message), or `:latest`
     is not HEAD yet.
3. **`These containers were created but never started`** — the retry did not
   clear compose's name conflict, and the stack may be down. Run
   `ssh documents 'docker ps -a --filter name=library'`; confirm any
   `<12 hex>_library-…` container is compose's (`docker inspect` labels
   `com.docker.compose.project=apps`, `State.StartedAt` 0001-01-01),
   `docker rm` it, and re-run `scripts/deploy.sh`.
4. **`/healthz` not OK** — `docker compose logs library-webserver`. Common
   causes: bad env in `/srv/apps/.env`, db not healthy yet (re-run after a few
   seconds), or a startup exception in new code.
