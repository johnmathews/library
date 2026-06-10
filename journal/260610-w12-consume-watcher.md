# W12 — Consume folder watcher

**Date:** 2026-06-10

## What landed

`library.consume.ConsumeWatcher`: watches `LIBRARY_CONSUME_DIR` with
`watchfiles.awatch` and ingests dropped files through the existing
`ingest_file` service (`source=consume`, no uploader). Runs as an
asyncio task inside the worker process (`library.worker`) when the env
var is set; unset = feature off, worker unchanged. Compose worker now
sets `LIBRARY_CONSUME_DIR=/data/consume`. New settings:
`consume_force_polling`, `consume_poll_interval_s`,
`consume_stability_s`, `consume_on_success` (archive|delete).
Docs: ingestion.md "Consume folder" section; architecture.md W12 done.

## Decisions

- **Never force-ingest an unstable file.** A file must have unchanged
  size+mtime for `consume_stability_s` before ingest (iOS Notes /
  Syncthing copies arrive incrementally). A file still changing after a
  5-minute cap is *skipped*, not "treated as stable": a partial copy
  would be stored under its own content hash as a permanently junk
  document (the completed file hashes differently, so dedup never
  repairs it). Skipped files are retried on their next filesystem event
  and by the startup sweep.
- **Startup sweep before watching.** `run()` processes every candidate
  already in the tree, covering files dropped while the worker was
  down.
- **Single-flight via an in-flight path set.** watchfiles emits both
  `added` and `modified` during a copy; events for a path already being
  processed are dropped (the in-flight run's stability wait observes
  further writes anyway). No dirty-retry queue: the only loss window is
  an event landing mid-process for a file that then times out — closed
  by the next event or sweep, fine for this workload.
- **Duplicates count as success**: the document is already in the
  library, so the file is archived like any other consume.
- **Failed files get no ingestion event**: `ingestion_events` requires
  a `document_id` and rejected files never create a row; `failed/` plus
  the warning log is the audit trail. Transient errors (DB down, I/O)
  keep the file in place for retry instead of moving it to `failed/`.
- **Archive layout** `consumed/YYYY/MM/<name>` (collision → `-N`
  suffix), inside the consume dir so Syncthing syncs the archive back
  to the device that dropped the file.
- **Worker integration**: `main()` keeps the plain sync path when the
  feature is off; otherwise `asyncio.run` drives
  `run_worker_async()` + watcher task, stop-event + await on shutdown.
  A watcher crash is logged and leaves the job worker running.

## Tests

`tests/test_consume.py` (13, real testcontainers Postgres +
InMemoryConnector): drop→document+archive, duplicate→archive without a
second row, growing file skipped until stable (background writer, short
injected stability/timeout), unsupported extension ignored in place,
unsniffable `.pdf`→`failed/`, oversize→`failed/`, Syncthing
temp/`.part`/dotfile/`consumed/`/`failed/` ignored, in-flight dedup,
real `run()` loop (sweep + live awatch event + clean stop), delete
mode, worker wiring with a stubbed Procrastinate app.
