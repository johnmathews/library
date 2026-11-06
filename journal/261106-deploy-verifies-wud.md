# The deploy script verifies WUD's deploys

> Purpose
>
> Records why `scripts/deploy.sh` stopped deploying by default, and what was
> measured on the way. Work unit W1 of the Library follow-ups run, which
> started as "the deploy runs the migration twice".

**Date:** 2026-11-06
**Branch:** `eng-deploy-verifies-wud`

## 1. What the first theory got wrong

The previous run saw the migration run twice on the 0041 deploy: a hash-prefixed
`<id>_library-migrate` started beside `library-migrate`, and one of them failed with
"expected to match one row ... 0 found". The theory was compose's recreate inside one
`docker compose up`. It does not survive a rehearsal.

- **Rehearsal:** a local stack with the production shape (explicit `container_name`s,
  web and worker `depends_on` the one-shot migrate with `service_completed_successfully`,
  the production image), a pending no-op migration that sleeps four seconds, and the
  old command `docker compose up -d library-migrate library-webserver library-worker`.
- **Result:** `docker events` showed one `start library-migrate`, then `die ... exit=0`.
  The renames were there (that is how compose recreates) but only one migration ran.

## 2. The real cause: two deployers

The host runs What's Up Docker (`wud`), set up by the home-server role
`document_library_lxc`. It polls ghcr every minute and runs
`/srv/apps/wud-deploy-library.sh` on a new `:latest`. That script does its own
`docker compose pull` and `up -d` of the same three services. None of the Library docs
mentioned it.

- **WUD's log for 2026-11-06** shows deploys at 07:44, 08:06, 08:32, 08:47 and 11:22 UTC.
  That is every promote of the day.
- **The collisions:** at 08:47 UTC (the 0041 deploy) WUD's compose failed with
  `No such container: cb8ba3b95f13...`, the same id as the stray migrate container. At
  11:22 UTC it failed on `4a827cb12f3d...` (`library-webserver`).
- **The race:** the manual `deploy.sh` and WUD ran two compose processes on one project
  at once, each with its own migrate job.
- **Probably also WUD:** the "another session deployed first" sightings recorded in
  memory `library-concurrent-deploys`.

## 3. Decision

Asked how deploys should work, John chose "WUD deploys, script verifies". The
alternatives were turning WUD off for library, or keeping both deployers behind a lock.

- **Default `scripts/deploy.sh`:** deploys nothing.
  - Runs the promote gate.
  - Waits up to 10 minutes for `/healthz` `git_sha` to equal HEAD.
  - Removes compose leftovers and checks for `Created` containers.
  - Checks the migration by state: `alembic_version` must equal `alembic heads` from the
    running image. This replaces reading `library-migrate`'s exit code, which says
    nothing about who migrated.
  - Reads the health body and prunes old images.
- **`--deploy` is the manual deploy.** One ssh session holds `flock` on `/srv/apps` for
  the whole sequence. The lock is on the directory, because WUD mounts it read-only.
  - Pull.
  - `docker compose run --rm -T library-migrate`, once.
  - `up -d --no-deps library-webserver library-worker`.
  - Then the same checks.
- **WUD's own script** takes the same lock and migrates once. That is W7, in home-server.
  Until it is applied, `--deploy` is safe only with WUD stopped.

## 4. Things found while building it

- **Stdin:** `--deploy`'s steps reach the host on `bash -s`'s stdin, and `compose run`
  attaches stdin by default, so it could swallow the rest of the script. Hence `-T
  </dev/null`.
  - The fixed steps ran in full through `bash -s` on the local stack: one migration,
    web and worker recreated, and the line after the migration ran.
  - Demonstrating the hazard without the fix did not work, because Docker Desktop
    failed the pull first ("lease does not exist"). It is the known behaviour, not an
    observed one.
- **`up --no-deps`** left the stale `library-migrate` container alone. It did not wait
  on it and did not re-run it.
- **Rollback:** WUD must be stopped first, or it redeploys `:latest` within a minute.
  The runbook now says so.

## 5. Tests

`tests/test_deploy_script.py` gained a fake `flock`, `compose run`/`pull`, `buildx` and
`alembic heads`/`psql` answers.

- **Seen red on the old script (10 failures):** `verify` did not exist, `compose run` was
  never called, and the old script read its exit code off the named migrate container.
- **Green on the new script (16 tests).** The new cases:
  - verify never runs a mutating command;
  - verify times out naming `--deploy`;
  - a DB revision behind the image fails in both modes;
  - `--deploy` takes one lock on the compose directory and runs `compose run` exactly
    once, in the order pull, run, up, with `up --no-deps` and no `library-migrate`;
  - a failed migration stops before `up`.
