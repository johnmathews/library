# Ingestion

How a file becomes a Document: upload → content-addressed storage →
database row → background job → status lifecycle. This document covers
the W3 implementation: storage layout, MIME handling, HEIC conversion,
the upload API, and the Procrastinate job queue.

> **No authentication yet.** The endpoints below are unauthenticated
> until W8 lands. Do not expose the API beyond a trusted network.

## Flow overview

```
POST /api/documents (multipart)
  │
  ├─ size check (LIBRARY_MAX_UPLOAD_BYTES, default 100 MB) ──► 413
  ├─ MIME detection (sniff content, fall back to client type) ─► 415 if unsupported
  ├─ sha256(content)
  ├─ duplicate? (non-deleted document with same sha256)
  │    └─ yes ─► log "duplicate_upload" event ─► 200 {duplicate: true}
  ├─ store original at /data/originals/ab/cd/<sha256>   (atomic, idempotent)
  ├─ HEIC/HEIF? ─► convert to JPEG ─► /data/derived/ab/cd/<sha256>/converted.jpg
  ├─ INSERT documents row (status=received) + "received" ingestion_event
  ├─ COMMIT, then defer process_document(document_id)
  └─ 201 {id, sha256, status, duplicate: false}

worker (python -m library.worker)
  └─ process_document(document_id)
       received ─► ocr ─► extract ─► indexed     (one ingestion_event per transition)
                └─ any error ─► failed (+ "failed" event with error detail)
```

## Content-addressed storage (`library.storage`)

Originals are stored by SHA-256 digest, fanned out over two directory
levels to keep directories small:

```
{LIBRARY_DATA_DIR}/originals/<sha[0:2]>/<sha[2:4]>/<sha256>
```

- **No file extension.** The MIME type and the original filename live in
  the `documents` row (`mime_type`, `original_filename`); the file on
  disk is pure content.
- **Atomic writes.** Content is written to a temporary file in the same
  directory and `os.replace`d into place, so a crash never leaves a
  half-written original under its final name.
- **Idempotent.** `store()` returns `created=False` when the hash
  already exists on disk and does not rewrite the file.

API (all functions accept an optional `data_dir` override; the default
comes from settings):

| Function | Behaviour |
| --- | --- |
| `store(content) -> StoreResult(sha256, path, created)` | Hash and persist bytes; no-op if already stored. |
| `path_for(sha256) -> Path` | Path of an original (validates the digest format; does not touch disk). |
| `open_original(sha256) -> BinaryIO` | Open an original for reading; `FileNotFoundError` if absent. |
| `derived_dir(sha256) -> Path` | Create (if needed) and return the derived-artifacts directory. |

Derived artifacts (conversions, future `searchable.pdf`, `thumb.webp`,
…) live next to — never inside — the originals tree:

```
{LIBRARY_DATA_DIR}/derived/<sha[0:2]>/<sha[2:4]>/<sha256>/<artifact>
```

## MIME detection and the allowed set

Accepted types: `application/pdf`, `image/jpeg`, `image/png`,
`image/heic`, `image/heif`, `image/tiff`, `text/plain`.

Detection (`library.ingest.detect_mime`) prefers content sniffing over
the client-declared type:

1. Sniff magic bytes with the **`filetype`** library (pure Python — no
   `libmagic` system dependency, unlike `python-magic`; it covers every
   binary type we accept, including HEIC).
2. `filetype` cannot identify plain text (it is magic-bytes based), so
   if sniffing fails and the content decodes as UTF-8, the type is
   `text/plain`.
3. Otherwise fall back to the client-declared type (normalised, e.g.
   `image/jpg` → `image/jpeg`).

Anything that resolves outside the allowed set is rejected with **415**.

## HEIC handling (`library.images`)

`normalize_image(content, mime) -> NormalizedImage(content, mime, converted)`

- Non-HEIC input is returned unchanged (`converted=False`).
- HEIC/HEIF is decoded with `pillow-heif`, orientation is applied
  (`ImageOps.exif_transpose`; pillow-heif also applies the HEIF
  orientation properties on decode), and the image is re-encoded as
  JPEG at quality 90 with the EXIF orientation tag cleared.

The **original HEIC bytes** are what gets content-addressed stored —
the conversion is written as the derived artifact `converted.jpg` under
`derived_dir(sha256)` and is what downstream steps (OCR, thumbnails)
will consume.

## Ingestion service (`library.ingest`)

`ingest_file(session, *, content, filename, mime=None, source,
uploader_id=None) -> IngestResult(document, duplicate)` is the single
entry point used by the upload endpoint (and, later, by the consume
folder, email, and MCP channels — hence `source`).

Steps: detect/validate MIME → hash → dedup check → store original →
write `converted.jpg` for HEIC → create `Document`
(`status=received`, `source`, `original_filename`, `mime_type`) →
append a `received` ingestion event → **commit** → defer
`process_document(document_id)`.

Decisions:

- **Dedup:** an existing, non-deleted document with the same sha256 is
  returned with `duplicate=True`; a `duplicate_upload` ingestion event
  is recorded against it (with the attempted filename/source), and no
  file or row is written.
- **Soft-deleted collision:** if the only match is soft-deleted, ingest
  raises `DeletedDuplicateError` (HTTP **409**) — `sha256` is unique in
  the schema, so re-inserting is impossible; restoring or purging the
  deleted document is a deliberate user action, not an upload side
  effect.
- **Commit before defer:** the job is deferred only after the document
  row is committed. Procrastinate uses its own connection, so deferring
  first would race the worker against an uncommitted row. The rare
  failure mode (commit succeeds, defer fails) leaves a `received`
  document with no job — visible in `/api/jobs` and re-queueable —
  which beats a job pointing at a row that does not exist.

## Job queue (Procrastinate)

- **Connector:** `procrastinate.PsycopgConnector` (async, psycopg 3) —
  the connector Procrastinate 3.x recommends and ships by default.
  Procrastinate's base dependency is pure-python `psycopg`, which needs
  the system `libpq` — absent from our `python:3.13-slim` runtime image —
  so the project depends on `psycopg[binary]` explicitly (bundled libpq).
  The SQLAlchemy URL from settings (`postgresql+asyncpg://…`) is
  translated to a plain libpq URL for it.
- **App wiring:** `library.jobs.job_app` is the Procrastinate app. The
  FastAPI lifespan opens/closes its connection pool so the API can
  `defer()`; the worker opens it itself.
- **Worker:** `python -m library.worker` runs the Procrastinate worker
  programmatically (this is the `worker` service command in
  docker-compose).

### `process_document(document_id)` — pipeline skeleton

W3 ships the lifecycle skeleton; real OCR (W4) and extraction (W6)
plug into the clearly named hooks `run_ocr(document)` and
`run_extraction(document)`, which currently no-op.

- Transitions: `received → ocr → extract → indexed`, one commit and one
  `status_changed` ingestion event (`detail: {"from": …, "to": …}`) per
  transition.
- Re-runs are idempotent: the pipeline resumes from the document's
  current status; an already-`indexed` (or `failed`) document is left
  untouched.
- Any exception marks the document `failed`, records a `failed` event
  with `{"error": …, "status": <status it failed in>}`, and re-raises so
  Procrastinate also marks the job failed. No automatic retries are
  configured in W3.

## HTTP API

### `POST /api/documents`

Multipart upload, field name `file`.

| Status | Meaning | Body |
| --- | --- | --- |
| 201 | New document ingested | `{id, sha256, status, duplicate: false}` |
| 200 | Duplicate of an existing document | `{id, sha256, status, duplicate: true}` — **200, not 201**, because no resource was created; the body points at the existing one |
| 409 | Same content as a soft-deleted document | error detail |
| 413 | Larger than `LIBRARY_MAX_UPLOAD_BYTES` (default 104857600) | error detail |
| 415 | MIME type not in the allowed set | error detail |

### `GET /api/jobs?limit=N`

Reads the `procrastinate_jobs` table directly (no auth — see banner):
returns the most recent jobs as
`[{id, status, task_name, attempts, scheduled_at, document_id}]`,
with `document_id` pulled from the job's JSON args when present.
`limit` defaults to 50 (max 500).

## Ingestion events

Append-only audit trail in `ingestion_events`:

| event | written by | detail |
| --- | --- | --- |
| `received` | ingest | `{filename, size, mime_type, source}` |
| `duplicate_upload` | ingest | `{filename, source}` |
| `status_changed` | pipeline | `{from, to}` |
| `failed` | pipeline | `{error, status}` |

## Configuration

| Env var | Default | Used for |
| --- | --- | --- |
| `LIBRARY_DATA_DIR` | `/data` | Root of `originals/` and `derived/` |
| `LIBRARY_MAX_UPLOAD_BYTES` | `104857600` (100 MB) | Upload size cap |
| `LIBRARY_DATABASE_URL` | (see `config.py`) | Database + job queue (translated for psycopg) |
