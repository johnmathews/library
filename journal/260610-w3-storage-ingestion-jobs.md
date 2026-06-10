# W3 — Storage service, ingestion core, job queue

**Date:** 2026-06-10 · **Unit:** W3 (improvement plan §1.3.3)

## What landed

- `library/storage.py` — content-addressed originals at
  `{data_dir}/originals/ab/cd/<sha256>` (atomic tmp-file + `os.replace`
  writes, idempotent `store`), `derived_dir()` helper for
  `{data_dir}/derived/ab/cd/<sha256>/`.
- `library/images.py` — HEIC/HEIF → JPEG (quality 90, orientation baked
  in via pillow-heif decode + `ImageOps.exif_transpose`).
- `library/ingest.py` — `ingest_file()`: mime sniff/validate → sha256 →
  dedup → store → Document(status=received) + `received` event →
  commit → defer `process_document`.
- `library/jobs.py` / `library/worker.py` — Procrastinate app
  (PsycopgConnector), `process_document` pipeline skeleton
  (received→ocr→extract→indexed with `status_changed` events, failure →
  `failed` + event; `run_ocr`/`run_extraction` hooks no-op until W4/W6).
  Worker runs via `python -m library.worker` (now the compose command).
- `POST /api/documents` (201 new / 200 duplicate / 409 deleted-dup /
  413 / 415) and `GET /api/jobs` reading `procrastinate_jobs`.
- Docs-first: `docs/ingestion.md` written before the code.

## Decisions

- **Mime sniffing: `filetype`** (pure Python) over `python-magic` — no
  libmagic system package in the slim image; covers all accepted binary
  types; `text/plain` falls back to a UTF-8 decode check.
- **Duplicate upload returns 200**, not 201 — nothing was created; body
  carries `duplicate: true` and the existing document. A
  `duplicate_upload` ingestion event is logged against it.
- **Soft-deleted hash collision → 409** (`sha256` is unique; restoring
  a deleted document should be deliberate, not an upload side effect).
- **Commit before defer** — Procrastinate defers on its own connection;
  deferring first would race the worker against an uncommitted row.
- **`psycopg[binary]` added explicitly**: procrastinate 3.8.1 depends on
  pure-python `psycopg`, which needs libpq at runtime — absent from
  `python:3.13-slim`. Caught by smoke-testing imports inside the built
  image; `psycopg-binary` bundles libpq. (The plan's suggested
  `procrastinate[psycopg]` extra does not exist in 3.8.1.)
- **Tests**: real Postgres (testcontainers) end-to-end — uploads defer
  through a real PsycopgConnector so tests assert actual
  `procrastinate_jobs` rows; the pipeline is tested by calling
  `advance_pipeline()` directly; `InMemoryConnector` covers task
  registration/defer routing and keeps unit tests DB-free (the app
  lifespan now opens the Procrastinate pool, so the default test app
  fixture swaps in the in-memory connector).

## Verification

- `uv run pytest` → 39 passed (20 integration).
- `ruff check` + `ruff format --check` clean; `docker compose config -q`
  clean; image rebuilt and all new modules import inside it.

## Follow-ups

- Compose stack has no migration-runner step yet; a fresh `docker
  compose up` needs `alembic upgrade head` run against the db before
  api/worker are functional.
- W4 replaces the `run_ocr` no-op; W6 `run_extraction`; W8 adds auth to
  the (currently open) endpoints.
