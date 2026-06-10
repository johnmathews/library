# Ingestion

How a file becomes a Document: upload → content-addressed storage →
database row → background job → status lifecycle. This document covers
storage layout, MIME handling, HEIC conversion, the upload API, the
Procrastinate job queue, the OCR stage (W4), and the Claude metadata
extraction stage (W6).

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

### `process_document(document_id)` — pipeline

- Transitions: `received → ocr → extract → indexed`, one commit and one
  `status_changed` ingestion event (`detail: {"from": …, "to": …}`) per
  transition. Entering `ocr` runs the OCR stage (below); entering
  `extract` runs Claude metadata extraction (see "Extraction").
- Stage hooks are `async def run_ocr(session, document)` /
  `run_extraction(session, document)` in `library.jobs`. The OCR work
  itself is CPU-bound and subprocess-heavy, so it runs in a thread via
  `asyncio.to_thread`, keeping the async worker responsive.
- Re-runs are idempotent: the pipeline resumes from the document's
  current status; an already-`indexed` (or `failed`) document is left
  untouched.
- Any exception marks the document `failed`, records a `failed` event
  with `{"error": …, "status": <status it failed in>}`, and re-raises so
  Procrastinate also marks the job failed. No automatic retries are
  configured yet.

## OCR (`library.ocr`)

Tesseract alone is not good enough (see the inception decisions: great
on clean scans, collapses on phone photos), so OCR is a **router over
three engines**, selected by input type, with a confidence gate.

```
run_ocr(document, original_path, derived_dir) -> OcrResult
  │
  ├─ text/plain ───────────► passthrough read              engine="text"
  ├─ application/pdf
  │    ├─ has text layer? (pypdfium2; avg chars/page ≥
  │    │  LIBRARY_TEXT_LAYER_MIN_CHARS_PER_PAGE, default 50)
  │    │     └─ yes ──────► direct extraction, no OCR      engine="text-layer"
  │    └─ no ─────────────► Tesseract path + confidence gate
  ├─ image/tiff ──────────► convert to PDF (img2pdf, Pillow fallback)
  │                          ─► Tesseract path + confidence gate
  ├─ image/jpeg, image/png ► photo path                    engine="rapidocr"
  └─ image/heic, image/heif ► photo path on the derived converted.jpg
```

`OcrResult` (`library.ocr.base`) carries `text`, `confidence` (0–100 or
`None`), `searchable_pdf` (path or `None`), `engine`, and `pages`. An
`OcrEngine` Protocol documents the engine call shape for future
implementations.

### Tesseract path (`library.ocr.tesseract`)

Runs **OCRmyPDF** as a subprocess (`python -m ocrmypdf`) with
`-l {LIBRARY_OCR_LANGUAGES} --rotate-pages --deskew --clean
--oversample 300 --skip-text --sidecar`. Output artifacts in the
document's derived dir: `searchable.pdf` (PDF/A with a text layer) and
`ocr.txt` (the sidecar text, which becomes `ocr_text`).

OCRmyPDF does not report word confidence, so a separate **confidence
probe** runs after it: up to 3 pages of the *produced* `searchable.pdf`
(i.e. after rotation/deskew, what Tesseract effectively saw) are
rasterized at 300 dpi with pypdfium2 and fed to `tesseract … tsv`; the
mean of per-word `conf` values (level-5 rows, conf ≥ 0, non-blank text)
is the document confidence. Subprocess + TSV was chosen over pytesseract
because it is the same thing with one fewer dependency.

System binaries required on this path: `tesseract` (+ `nld`/`eng`
tessdata), `ghostscript` (PDF/A output), `unpaper` (`--clean`). They are
installed in the runtime Docker image and in CI.

### Photo path (`library.ocr.photo`)

For camera shots (and the HEIC-derived JPEG). OpenCV preprocessing:
grayscale → page contour detection (largest 4-point contour covering
≥ 30% of the frame) → 4-point perspective transform when found → CLAHE
contrast enhancement. Then **RapidOCR** (PP-OCRv5 `latin` recognition
model — covers Dutch+English in one model — on ONNX Runtime, CPU).
Boxes are sorted by (top-y, left-x) for reading order; text is joined
line-per-box; confidence is the mean of per-box scores scaled to 0–100.
This path produces no searchable PDF (`searchable_pdf=None`).

RapidOCR downloads its models on first use and caches them; the worker
image works offline after the first run.

### Confidence gate

If the Tesseract path's mean word confidence is below
`LIBRARY_OCR_CONFIDENCE_THRESHOLD` (default 65.0) — or no words were
found at all — the router rasterizes the PDF pages at 300 dpi and
retries them through the photo path, keeping whichever result has the
higher confidence. The `searchable.pdf` artifact from the Tesseract run
is kept either way (it is still the right viewing artifact; only
`ocr_text`/`ocr_confidence` come from the winning engine).

### Persistence

The `run_ocr` job hook stores the result on the document: `ocr_text`,
`ocr_confidence`, `page_count`, and `searchable_pdf` (boolean: the
artifact exists in the derived dir). It appends an `ocr_completed`
ingestion event with `{engine, confidence, pages, characters}` — the
"born-digital PDF skipped OCR" assertion is `engine == "text-layer"`
in that event. On error it appends `ocr_failed` with the error message
and re-raises (so the standard `failed` handling also applies).

## Extraction (`library.extraction`)

The `extract` pipeline stage turns OCR text into structured metadata
with Claude (decision 3 in the improvement plan): kind, sender, title,
summary, dates, total amount + currency, language, suggested tags. The
guiding invariant is that **extraction is best-effort**: whatever
happens here — API failure, unusable input, budget exhausted, feature
disabled — the document still reaches `indexed` and stays searchable by
its OCR text. Only the pipeline machinery itself can fail a document.

### Models and structured outputs

`client.messages.parse()` (async SDK, structured outputs GA) is called
with the Pydantic schema `library.extraction.schema.ExtractedMetadata`;
the SDK converts the model to a JSON schema (with
`additionalProperties: false`) and returns a validated instance via
`response.parsed_output`. The SDK retries 429/5xx itself.

- **Primary model:** `claude-haiku-4-5` ($1/$5 per MTok).
- **Escalation:** if the parsed result reports `confidence: "low"`, or
  the response fails to parse/validate, the document is retried **once**
  on `claude-sonnet-4-6` ($3/$15 per MTok). The escalated result is used
  even if its confidence is also low (there is nothing better to do).

### Schema (`library.extraction.schema.ExtractedMetadata`)

| Field | Type | Notes |
| --- | --- | --- |
| `kind_slug` | enum | The 11 seeded kind slugs (`invoice` … `other`); enforced by the JSON schema, so the model cannot invent kinds. |
| `sender_name` | `str \| None` | Canonical short organisation/person name. |
| `title`, `summary` | `str` | In the document's own language; summary ≤ 2 sentences (prompt-enforced — string length constraints are not supported in structured-output schemas). |
| `document_date`, `due_date`, `expiry_date` | `date \| None` | Wire format is an ISO `YYYY-MM-DD` string; a before-validator trims it and maps empty/placeholder values to `None`, so sloppy-but-harmless output degrades to "no date" while a truly malformed date raises (and triggers escalation). |
| `amount_total` | `str \| None` | Decimal string, parsed defensively (currency symbols stripped, `12,50` → `12.50`); unparseable values become `None` rather than failing the document. Converted to `Decimal` when applied. |
| `currency` | `str \| None` | ISO 4217; anything that is not three letters becomes `None`. |
| `language` | enum | `nld` / `eng` / `mixed` / `unknown`. |
| `tags` | `list[str]` | Normalised to lowercase slugs, deduplicated, capped at 8 client-side (array length constraints are also unsupported in the schema). |
| `confidence` | enum | `high` / `medium` / `low` — `low` triggers escalation. |
| `reasoning_note` | `str \| None` | One-line note when something needed judgement. |

Numeric min/max, string-length, and array-length constraints are not
supported by structured outputs, so everything of that shape is either
prompt-instructed or normalised by validators.

### Input selection

- **Normal case:** the user message is the document's `ocr_text`,
  truncated to 8,000 characters (≈ 2–3k tokens — plenty for metadata,
  caps spend on huge documents).
- **Garbage/empty OCR** (stripped text < 20 chars) and the original is
  a PDF or image: the original file is sent directly as a base64
  `document` (PDF) or `image` content block. HEIC/HEIF uses the derived
  `converted.jpg` (Claude does not accept HEIC). Files over 5 MB are
  not sent — extraction is skipped gracefully (`reason:
  "file_too_large"`). TIFF and text without usable OCR text are skipped
  with `reason: "input_unusable"`.

### Prompt

A versioned system prompt (`PROMPT_VERSION` in
`library.extraction.extractor`) describes Library, the kinds taxonomy,
and the Dutch+English household-paperwork domain, and instructs concise
title/summary **in the document's language**. The prompt version, model
and token usage of every run are stored on the document
(`extra["extraction"]`) and in the audit trail, so a future prompt
change can identify documents extracted with an older prompt.

### Applying results (`library.extraction.apply`)

On success:

- **Sender** is upserted by case-insensitive name match (create if new).
- **Kind** is resolved by slug to the seeded `kinds` row.
- **Tags** are get-or-created by slug and merged into the document's
  tag set (never removed).
- Scalar fields (`title`, `summary`, dates, `amount_total`, `currency`,
  `language`) are set; `None` extraction values never null out existing
  data, and `language` is only set when not `unknown`.
- `extra["extraction"]` records `{prompt_version, model, confidence,
  input_tokens, output_tokens, cost_usd, escalated, input_mode,
  fields_set, reasoning_note}`.

**User edits win.** Re-extraction overwrites previous *extraction*
values but never user-edited ones: any field name listed in
`extra["user_edited_fields"]` (populated by the metadata-editing
surfaces of W7/W11) is skipped, and `fields_set` records which fields
the extractor actually wrote.

### Re-running extraction

`library.jobs.extract_document(document_id)` is a Procrastinate task
that re-runs extraction for one document (e.g. after a prompt upgrade),
independent of pipeline status. It is idempotent in the sense above:
extraction-owned fields are overwritten, user-edited fields and
existing tags are preserved.

```python
from library.jobs import extract_document
await extract_document.defer_async(document_id=123)
```

### Failure, skip, and cost handling

| Condition | Event | Document |
| --- | --- | --- |
| `LIBRARY_EXTRACTION_ENABLED=false` | `extraction_skipped` `{reason: "disabled"}` | continues to `indexed` |
| No `LIBRARY_ANTHROPIC_API_KEY` | `extraction_skipped` `{reason: "missing_api_key"}` | continues to `indexed` |
| Daily budget spent | `extraction_skipped` `{reason: "budget", spent_usd, budget_usd}` | continues to `indexed` |
| Unusable/oversized input | `extraction_skipped` `{reason: "input_unusable" \| "file_too_large"}` | continues to `indexed` |
| API error after SDK retries, double parse failure | `extraction_failed` `{error, prompt_version}` | continues to `indexed` |
| Success | `extraction_completed` `{model, prompt_version, confidence, input_tokens, output_tokens, cost_usd, escalated, input_mode}` | metadata populated |

**Cost guard:** every completed call stores its estimated cost
(`cost_usd`, from the pricing constants above) in the event detail.
Before each extraction, one query sums today's `cost_usd` across
`ingestion_events`; at or over `LIBRARY_EXTRACTION_DAILY_BUDGET_USD`
(default 5.0) extraction is skipped with `reason: "budget"` until the
next UTC day.

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
| `ocr_completed` | OCR stage | `{engine, confidence, pages, characters}` |
| `ocr_failed` | OCR stage | `{error}` |
| `extraction_completed` | extraction stage | `{model, prompt_version, confidence, input_tokens, output_tokens, cost_usd, escalated, input_mode}` |
| `extraction_skipped` | extraction stage | `{reason, ...}` — `disabled`, `missing_api_key`, `budget`, `input_unusable`, `file_too_large` |
| `extraction_failed` | extraction stage | `{error, prompt_version}` |
| `failed` | pipeline | `{error, status}` |

## Configuration

| Env var | Default | Used for |
| --- | --- | --- |
| `LIBRARY_DATA_DIR` | `/data` | Root of `originals/` and `derived/` |
| `LIBRARY_MAX_UPLOAD_BYTES` | `104857600` (100 MB) | Upload size cap |
| `LIBRARY_DATABASE_URL` | (see `config.py`) | Database + job queue (translated for psycopg) |
| `LIBRARY_OCR_LANGUAGES` | `nld+eng` | Tesseract language pack(s) (`-l` value) |
| `LIBRARY_OCR_CONFIDENCE_THRESHOLD` | `65.0` | Below this mean word confidence, retry via the photo path |
| `LIBRARY_TEXT_LAYER_MIN_CHARS_PER_PAGE` | `50` | Avg chars/page for a PDF to count as born-digital (skip OCR) |
| `LIBRARY_ANTHROPIC_API_KEY` | unset | Anthropic API key; extraction is skipped when absent |
| `LIBRARY_EXTRACTION_ENABLED` | `true` | Master switch for the extraction stage |
| `LIBRARY_EXTRACTION_MODEL` | `claude-haiku-4-5` | Primary extraction model |
| `LIBRARY_EXTRACTION_ESCALATION_MODEL` | `claude-sonnet-4-6` | Retry model on low confidence / parse failure |
| `LIBRARY_EXTRACTION_DAILY_BUDGET_USD` | `5.0` | Estimated daily API spend cap; over budget → skip with `reason: "budget"` |
