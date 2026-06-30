# Deploy runbook

**Status:** active. **Last updated:** 2026-06-30. **Supersedes:** none.

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
2. **Key-based SSH to the host works** (`ssh paperless true` returns instantly,
   no password). The script aborts early if it can't connect non-interactively.
3. **No schema-incompatible change shipped without a backup plan.** The new
   migrations run automatically (step 1.3). They are normally additive and have
   down-migrations, but if a migration is destructive or risky, take a DB backup
   first (see [../deployment.md](../deployment.md) §1.6).

## 1.3 What `scripts/deploy.sh` does

On the host, in `/srv/apps`, it runs:

```bash
docker compose up -d --pull always library-migrate library-webserver library-worker
```

then verifies:

1. **`--pull always`** fetches the freshly-promoted `:latest`.
2. **`library-migrate`** (one-shot) applies any new Alembic migrations
   transactionally, then exits. The script reads its exit code and **aborts if
   it is non-zero** (web/worker would otherwise run against an un-migrated DB).
3. **`library-webserver` + `library-worker`** are recreated on the new image.
4. **`GET /healthz`** must return OK.
5. Prints the running images and the prod Alembic head.

Service names on the live host are `library-*` (the repo's compose file uses
`api`/`worker`/`db`; production renames them — see the deployment doc). The
script uses the production names.

## 1.4 Other modes

```bash
scripts/deploy.sh --status   # show the running stack + Alembic head, no deploy
scripts/deploy.sh --logs     # tail recent webserver + worker logs
scripts/deploy.sh --help     # usage
```

Overrides (env): `LIBRARY_DEPLOY_HOST` (default `paperless`),
`LIBRARY_DEPLOY_DIR` (default `/srv/apps`).

## 1.5 Verify after deploy

The script already checks migrate + `/healthz`, but for a human sanity pass:

```bash
scripts/deploy.sh --status        # web/worker/db healthy, head = expected revision
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
