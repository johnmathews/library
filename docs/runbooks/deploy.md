# Deploy runbook

**Status:** active. **Last updated:** 2026-08-13 (§1.2: the `main` ruleset does require `ci-gate` — the claim that branch protection was not yet pointed at it was written before the ruleset existed; documented the `check_mypy.py` ratchet as the second `typecheck` step, now guarding an empty quarantine). Earlier: 2026-08-12, 2026-07-27. **Supersedes:** none.
**Last verified:** 2026-08-28 — method: partial, scoped to §1.3 step 4. Added the /healthz body check to match the new block in `scripts/deploy.sh` (verified with `bash -n`, and the two grep patterns proven against a real compact `/healthz` payload). §1.2 and the deploy-script flags carry forward the 2026-08-13 verification below, whose method was: method: partial, scoped to §1.2. Re-read the "what green means" paragraph against `ci.yml` and `scripts/ci_gate.sh`, and checked the branch-protection claim against the live `main` ruleset via `gh api repos/johnmathews/library/rulesets` (active since 2026-07-28; requires `ci-gate`, blocks deletion and non-fast-forward, `bypass_actors: []`). The deploy-script flags and the end-to-end run remain as verified on 2026-08-12 and were not re-executed.
**Covers:** scripts/deploy.sh, scripts/ci_gate.sh

How to ship a merged change to the live `paperless` LXC. This is the focused
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

   **What "green" means.** The run's aggregator job is **`ci-gate`**, which
   passes the `needs.<job>.result` of every other job except `promote` —
   including `docs-stamps` and `typecheck` — to `scripts/ci_gate.sh`. That
   script tolerates `skipped` for the path-filtered jobs (`backend`,
   `frontend`, `e2e`, `compose-smoke`, `build`, `typecheck`) — a skipped
   *required* check would block a merge forever — but requires **`changes`**
   (the path-filter job all the others declare `needs:` on) to be exactly
   `success`, since a broken `changes` skips every one of them and would
   otherwise leave the gate nothing to reject. `tests/test_ci_gate.py` exercises those cases.
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
2. **Key-based SSH to the host works** (`ssh paperless true` returns instantly,
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

Then, on the host, in `/srv/apps`, it runs:

```bash
docker compose up -d --pull always library-migrate library-webserver library-worker
```

then verifies:

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
5. Prints the running images and the prod Alembic head.

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

Overrides (env): `LIBRARY_DEPLOY_HOST` (default `paperless`),
`LIBRARY_DEPLOY_DIR` (default `/srv/apps`), `SKIP_PROMOTE_CHECK` (`1` = bypass
the promote gate, like `--force`).

## 1.5 Verify after deploy

The script already checks migrate + `/healthz`, but for a human sanity pass:

```bash
scripts/deploy.sh --status        # web/worker/db/embedder up, head = expected revision
ssh paperless 'cd /srv/apps && docker compose logs --tail 30 library-worker'
```

Then click through the changed surface in the browser.

## 1.6 Rollback

The image is content-addressed by commit SHA, so rollback = redeploy the
previous SHA:

1. Find the previous good image:
   ```bash
   ssh paperless 'docker images ghcr.io/johnmathews/library --format "{{.Tag}}\t{{.CreatedAt}}"'
   ```
2. Pin the stack to the previous `:sha` tag (edit `/srv/apps/.env` or the compose
   image ref — back it up first) and `docker compose up -d`. See
   [../deployment.md](../deployment.md) §1.7.
3. **Migrations do not auto-roll-back.** If the bad deploy ran a migration that
   the old image can't tolerate, downgrade it explicitly
   (`docker compose run --rm library-migrate alembic downgrade -1`) or restore
   the pre-deploy DB backup (§1.6 of the deployment doc).

## 1.7 Troubleshooting

1. **`Cannot SSH to 'paperless'`** — `ssh paperless true` fails. Fix your SSH
   config/keys, or set `LIBRARY_DEPLOY_HOST`.
2. **`library-migrate exited <n>`** — a migration failed; the script aborts
   before declaring success. Read `docker compose logs library-migrate`, fix
   forward or roll back. The DB is left at whatever revision the failed
   transaction reached (migrations are transactional, so a failed step rolls
   itself back).
3. **`/healthz` not OK** — `docker compose logs library-webserver`. Common
   causes: bad env in `/srv/apps/.env`, db not healthy yet (re-run after a few
   seconds), or a startup exception in new code.
