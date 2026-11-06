# The migrate run must not start the database

> Purpose
>
> Records what the first deploy under the new single-deployer setup showed, and the
> one-flag fix that followed. A follow-up to `261106-deploy-verifies-wud.md` (W1 and W7
> of the Library follow-ups run).

**Date:** 2026-11-07
**Branch:** `eng-deploy-migrate-no-deps`

## 1. The first locked WUD deploy

WUD deployed `045b235` at 23:17 UTC on 2026-11-06 with its new script. That script takes the
`/srv/apps` lock, then runs `docker compose run --rm -T library-migrate`, then `up -d --no-deps`
for web and worker. `scripts/deploy.sh` (verify) passed afterwards: HEAD was live,
`alembic_version` equalled the image head, and there was no stray container. The migration ran
once.

WUD's log also showed `Container library-db Recreate ... Started ... Healthy` before the run
container was created. That is a Postgres restart under the old, still-running web and worker.

## 2. Cause

`compose run` starts the service's dependencies, and `library-migrate` depends on `library-db`.
The host runs Docker Compose 2.39 and WUD's image runs 5.6. They compute different config hashes
for the same file, so each one treats a container created by the other as changed and recreates
it. The database had last been created by the 00:12 CET role apply, so WUD's compose recreated
it. The old `up -d library-migrate ...` sequence did the same on some deploys (08:06 and 08:47
UTC on 2026-11-06).

## 3. Fix

`--no-deps` on the run, in `scripts/deploy.sh --deploy`, and on home-server's WUD script and
`make library-upgrade`. The database is always running already. If it is not, the migration
fails to connect and the script stops before touching web and worker, which is the safe
outcome. Aligning the two compose versions would also work, but it changes the WUD image build,
and `--no-deps` is enough once neither deployer starts dependencies.

The test that pins `--deploy`'s single run now expects
`docker compose run --rm --no-deps -T library-migrate`. It was seen red on the previous script.
