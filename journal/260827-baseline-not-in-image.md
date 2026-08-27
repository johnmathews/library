# Committed is not shipped

**Date:** 2026-08-27
**Branch:** `fix/ship-recall-baseline-in-image`

## What happened

`recall-baseline.json` was committed in PR #103 and the change deployed. I had
told the user the deploy mattered because it would put the baseline into the
image, so the next `eval-recall` run would print deltas against it.

It did not. Checking the running container after the deploy:

```
$ docker compose exec -T library-webserver cat /app/recall-baseline.json
cat: /app/recall-baseline.json: No such file or directory
```

The `Dockerfile` copies `pyproject.toml`, `uv.lock`, `README.md`, `alembic.ini`,
`migrations/`, `src/`, `scripts/`, the built SPA, top-level `docs/*.md` and an
optional coverage summary. Nothing copies a file added at the repository root.
Committing it changed the repository and nothing else.

The claim that the deploy would ship it was asserted without reading the
`Dockerfile`. The file's own path (`RECALL_BASELINE_PATH`, `parents[2]` →
`/app`) was checked; the question of how it would ever *get* to `/app` was not.
`eval-recall --write-baseline` writes there at runtime, which is why the path
looked verified — a write that had happened proved the destination, not the
build.

## The fix

A `COPY` for the baseline, using the same bracket-glob trick the coverage
summary already uses so a checkout without a baseline still builds:

```dockerfile
COPY --chown=app:app recall-baselin[e].json /app/
```

And, because this is precisely the class of thing that is invisible until
someone looks, an assertion in the `compose-smoke` CI job that the file exists
in the built image and parses with the fields the delta report reads. That job
already builds the real image and runs the real stack, so it is the cheapest
place to prove a COPY landed.

## Worth remembering

A missing baseline is silent. `_report_recall` treats "no baseline file" as "no
previous run" and prints its numbers with no delta and no complaint — the same
output a first-ever run produces. Nothing would have surfaced this except
looking inside the container.

The general shape: **"committed" and "shipped" are different claims, and only
one of them was checked.** For anything read at runtime from a path inside the
image, the question is not whether the file is in the repository but whether a
`COPY` puts it there — and the answer belongs in CI, not in a memory of having
looked once.
