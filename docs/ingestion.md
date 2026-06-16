# Ingestion

How a file becomes a Document: upload → content-addressed storage →
database row → background job → status lifecycle. This document covers
storage layout, MIME handling, HEIC conversion, the upload API, the
Procrastinate job queue, the OCR stage (W4), the Claude metadata
extraction stage (W6), the consume folder watcher (W12), and email-in
ingestion (W14).

> **Authentication.** Every endpoint below requires a session cookie or
> bearer API token — see [api.md §1.9](api.md).

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
       received ─► ocr ─► extract ─► embed ─► indexed   (one ingestion_event per transition)
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

- Transitions: `received → ocr → extract → embed → indexed`, one commit and
  one `status_changed` ingestion event (`detail: {"from": …, "to": …}`) per
  transition. Entering `ocr` runs the OCR stage (below); entering `extract`
  runs Claude metadata extraction (see "Extraction"); entering `embed` chunks
  the text and stores bge-m3 vectors for semantic search (best-effort — a
  document that fails to embed still reaches `indexed`; see [ask.md](ask.md)).
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

### `generate_thumbnail(document_id)` — first-page thumbnail (W7)

Deferred by the pipeline immediately after the OCR stage succeeds (it
needs nothing from extraction, so it runs in parallel with that stage).
Renders page 1 — pypdfium2 for PDFs, Pillow for images, HEIC via the
derived `converted.jpg` — to a ~480 px wide WebP at
`derived/<sha>/thumb.webp` and records a `thumbnail_generated` (or
`thumbnail_skipped`, e.g. for `text/plain`) event. The file's existence
is the only thumbnail marker; `GET /api/documents/{id}/thumbnail` serves
it (see [api.md](api.md)).

## OCR (`library.ocr`)

Tesseract alone is not good enough (see the inception decisions: great
on clean scans, collapses on phone photos), so OCR is a **router over
three engines**, selected by input type, with a confidence gate.

```
run_ocr(document, original_path, derived_dir) -> OcrResult
  │
  ├─ text/plain ───────────► passthrough read              engine="text"
  ├─ application/pdf ──────► analyze_pdf (library.ocr.analysis)
  │    ├─ text layer (avg chars/page ≥
  │    │  LIBRARY_TEXT_LAYER_MIN_CHARS_PER_PAGE, default 50)
  │    │  AND not scan-like ► direct extraction, no OCR    engine="text-layer"
  │    └─ scan-like, or too little text
  │         ─► Tesseract path + confidence gate
  │            (--redo-ocr when any embedded text exists)
  │            └─ on OCRmyPDF/Tesseract FAILURE with a usable
  │               text layer ► embedded text   engine="text-layer-fallback"
  ├─ image/tiff ──────────► convert to PDF (img2pdf, Pillow fallback)
  │                          ─► Tesseract path + confidence gate
  ├─ image/jpeg, image/png ► photo path                    engine="rapidocr"
  └─ image/heic, image/heif ► photo path on the derived converted.jpg
```

`OcrResult` (`library.ocr.base`) carries `text`, `confidence` (0–100 on
the producing engine's own scale, or `None`), `searchable_pdf` (path or
`None`), `engine`, `pages`, and `gate` (both engines' confidences when
the confidence gate retried; `None` otherwise). An `OcrEngine` Protocol
documents the engine call shape for future implementations.

### Scan detection (`library.ocr.analysis`)

The W5 benchmark ([260610-ocr-benchmark.md](benchmarks/260610-ocr-benchmark.md))
found that iOS Notes scan exports — the primary input type — carry an
embedded Apple-OCR text layer of mediocre quality, so "has a text layer"
alone cannot decide the route. `analyze_pdf` makes one pass over the PDF
and classifies:

- a page is **image-backed** when a single raster image covers ≥ 50% of
  the page area;
- a document is **scan-like** when ≥ 50% of its pages are image-backed.

Scan-like PDFs are always OCRed, even when they carry a text layer; the
embedded text is only used as a fallback if OCR itself fails (engine
`text-layer-fallback` — mediocre text beats failing the document). A
born-digital report with a few embedded scan pages stays below the 50%
page fraction and keeps the text-layer route. The benchmark validated
both directions on the real corpus (10/10 scans detected, 6/6
born-digital documents untouched).

### Tesseract path (`library.ocr.tesseract`)

Runs **OCRmyPDF** as a subprocess (`python -m ocrmypdf`) with
`-l {LIBRARY_OCR_LANGUAGES} --rotate-pages --clean --oversample 300
--sidecar` plus a mode flag set:

- **default** (`redo=False`): `--deskew --skip-text` — image-only input,
  nothing to re-OCR;
- **redo** (`redo=True`, chosen by the router when the PDF has any
  embedded text): `--redo-ocr` — replaces existing (invisible) OCR text
  instead of skipping pages that have text. OCRmyPDF 17.x rejects
  `--redo-ocr` combined with `--deskew`, `--clean-final` or
  `--remove-background`, so the redo set drops `--deskew`; that is fine
  for its targets (scan-app exports are already deskewed and cropped by
  the app), and plain `--clean` still applies to the image fed to
  Tesseract.

Output artifacts in the document's derived dir: `searchable.pdf` (PDF/A
with a text layer) and `ocr.txt` (the sidecar text, which becomes
`ocr_text`).

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
retries them through the photo path. Tesseract word confidence and
RapidOCR box confidence are **not comparable** (the W5 benchmark measured
RapidOCR near-constant at 97–99 regardless of quality), so the winner is
decided by text yield, never by cross-engine confidence: the retry is
kept iff it produced at least 0.8× Tesseract's character count
(`RETRY_MIN_TEXT_RATIO`), otherwise Tesseract's result stands. Both raw
confidences are recorded in `OcrResult.gate` (and the `ocr_completed`
event detail); the retained `confidence` stays on the chosen engine's
own scale, with `engine` naming which. The `searchable.pdf` artifact
from the Tesseract run is kept either way (it is still the right viewing
artifact; only `ocr_text`/`ocr_confidence` come from the winning
engine).

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

## Consume folder (`library.consume`, W12)

A watched drop directory: anything placed there is ingested through the
same `ingest_file` service as an upload (`source=consume`, no uploader).
This is the primary scanner flow — iOS Notes scan exports land in a
Syncthing-synced folder that is (or is mounted at) the consume dir.

The watcher runs **inside the worker process**: when
`LIBRARY_CONSUME_DIR` is set, `python -m library.worker` starts a
`ConsumeWatcher` task alongside the Procrastinate worker (same event
loop, clean shutdown together). Unset (the default) the feature is off
and the worker behaves exactly as before.

### Flow

```
file appears in {LIBRARY_CONSUME_DIR}
  │
  ├─ candidate? (supported extension; not a dotfile / Syncthing temp /
  │   *.part; not under consumed/ or failed/) ── no ─► ignored entirely
  ├─ stability wait: size+mtime must be unchanged for
  │   LIBRARY_CONSUME_STABILITY_S (re-sampled until stable, up to a
  │   5-minute cap) ── still changing at the cap ─► skipped; retried on
  │   the next filesystem event (or the next startup sweep)
  ├─ ingest_file(content, filename, source=consume)
  │    ├─ new document ─► row + "received" event + process_document job
  │    └─ duplicate    ─► "duplicate_upload" event on the existing row
  ├─ success (incl. duplicate) ─► move to consumed/YYYY/MM/  (or unlink
  │   when LIBRARY_CONSUME_ON_SUCCESS=delete)
  └─ rejected (unsupported MIME, oversize, soft-deleted duplicate)
      ─► move to failed/ + warning log
```

Details and decisions:

- **Stability before ingest.** iOS Notes/Syncthing copies arrive
  incrementally. The watcher samples `(size, mtime)` and only ingests
  once two samples `LIBRARY_CONSUME_STABILITY_S` apart match. A file
  still growing after ~5 minutes is **skipped, not force-ingested**:
  ingesting a partial copy would store truncated bytes under their own
  content hash (a junk document that dedup can never repair, since the
  complete file hashes differently). Skipping is safe — the writer's
  next write emits another event, and the startup sweep catches
  anything that completed while nobody was watching.
- **Startup sweep.** Before watching, the watcher recursively processes
  every candidate file already present — files dropped while the worker
  was down are not lost.
- **Single-flight.** An in-flight set keyed on path makes duplicate
  events for the same file (watchfiles emits add *and* modify during a
  copy) result in one ingest. Events arriving for a path already being
  processed are dropped; the stability wait inside the in-flight
  processing observes any further writes anyway.
- **Ignored names.** Dotfiles (any path component starting with `.`,
  which covers Syncthing's `.syncthing.<name>.tmp` temps), Syncthing's
  legacy `~syncthing~<name>.tmp` pattern, `*.part` partial downloads,
  and everything under the `consumed/` and `failed/` subdirectories.
  Syncthing writes to a temp name and renames into place, so the
  finished file appears atomically as a single `added` event.
- **Duplicates are consumed.** A file whose content already exists gets
  a `duplicate_upload` event on the existing document and is archived
  like a success — the document is in the library, so the drop is done.
- **Failures.** Unsupported MIME, content over
  `LIBRARY_MAX_UPLOAD_BYTES`, or a soft-deleted-duplicate collision
  move the file to `failed/` with a warning log. No ingestion event is
  written — these paths never create a document row, and
  `ingestion_events.document_id` is non-nullable, so the `failed/` dir
  plus the log *is* the audit trail. Transient errors (database down,
  I/O) do **not** go to `failed/`: the file stays in place and is
  retried on a later event or the next sweep.
- **Per-file isolation.** All per-file exceptions are caught and logged
  inside the watcher loop; nothing a dropped file does can take down
  the Procrastinate worker sharing the process.

### Archive layout

Successful files are moved (same filesystem, `os.replace`) to
`{consume_dir}/consumed/YYYY/MM/<original name>` — year/month of
consumption, so the archive stays browsable and Syncthing keeps syncing
it back to the devices that dropped the files. Failures go to
`{consume_dir}/failed/<original name>`. Name collisions get a numeric
suffix (`scan-1.pdf`). Set `LIBRARY_CONSUME_ON_SUCCESS=delete` to
unlink instead of archiving.

### Supported extensions

`.pdf .jpg .jpeg .png .heic .heif .tif .tiff .txt` (case-insensitive).
Anything else is ignored in place — extensionless or unknown files are
*not* moved to `failed/`, because Syncthing folders routinely contain
foreign files (e.g. `.stfolder`) that are not ours to touch. Content
validation still happens at ingest: a `.pdf` that is not actually a PDF
is rejected by MIME sniffing and lands in `failed/`.

### Syncthing / NAS notes

- The compose worker sets `LIBRARY_CONSUME_DIR=/data/consume` inside
  the shared `/data` volume. In production, bind-mount your
  Syncthing-synced folder over it (e.g.
  `- /srv/syncthing/scans:/data/consume`).
- **inotify does not cross NFS/SMB.** If the consume dir is a network
  mount (NAS export, SMB share), file events never reach the container —
  set `LIBRARY_CONSUME_FORCE_POLLING=true` so watchfiles polls instead
  (`LIBRARY_CONSUME_POLL_INTERVAL_S` controls the poll cadence). A
  local bind mount synced by a Syncthing *on the same host* does not
  need polling.

## Email-in (`library.email_ingest`, W14)

A periodic Procrastinate task (`library.jobs.poll_email_inbox`) polls an
IMAP mailbox and ingests every supported attachment through the same
`ingest_file` service as an upload (`source=email`, no uploader). The
feature is off until `LIBRARY_EMAIL_HOST` is set — the schedule still
ticks (cron built from `LIBRARY_EMAIL_POLL_MINUTES`, default
`*/10 * * * *`), but the task returns immediately.

### Flow

```
poll_email_inbox fires (every LIBRARY_EMAIL_POLL_MINUTES minutes)
  │
  ├─ LIBRARY_EMAIL_HOST unset ─► instant no-op
  ├─ connect (IMAP over TLS, port 993) and select LIBRARY_EMAIL_FOLDER;
  │   create LIBRARY_EMAIL_PROCESSED_FOLDER if missing
  └─ for every message in the folder (ALL, seen flags untouched):
       ├─ sender not in LIBRARY_EMAIL_ALLOWED_SENDERS (when non-empty)
       │   ─► skipped; left in place (visible to the operator)
       ├─ per attachment: sniff MIME; if in the allowed set and within
       │   LIBRARY_MAX_UPLOAD_BYTES ─► ingest_file(source=email) — new
       │   document or `duplicate_upload` on the existing one; anything
       │   else is skipped with a log line
       ├─ move the message to LIBRARY_EMAIL_PROCESSED_FOLDER (also for
       │   body-only mails — v1 ingests attachments only)
       └─ any per-message error ─► logged, message left in place for
           the next poll; the run continues with the next message
```

Details and decisions:

- **Idempotency is folder-based.** The move to the processed folder is
  what stops a message being scanned twice — not the IMAP seen flag,
  which a human reading the same mailbox would clobber. Content dedup
  in `ingest_file` (sha256) backs this up: if the same attachment
  arrives in a different mail, the existing document gets a
  `duplicate_upload` event and the new mail is still filed away.
- **Sender hint for extraction/audit.** The recorded
  `received`/`duplicate_upload` event carries `email_from`,
  `email_subject`, and `email_message_id` alongside the standard keys
  (via `ingest_file`'s `extra_event_detail` parameter).
- **Allowlist.** `LIBRARY_EMAIL_ALLOWED_SENDERS` is comma-separated and
  case-insensitive; empty (default) accepts mail from anyone, so set it
  whenever the address is guessable. Rejected mail stays in the inbox
  (it would otherwise vanish silently on a sender typo) — expect a
  warning log per poll until it is dealt with.
- **Attachments only (v1).** Body-only mails create no document; they
  are still moved to the processed folder. HTML-to-PDF of mail bodies
  was considered and deferred (improvement plan §1.3.14).
- **Sync IMAP off the worker loop.** imap-tools is synchronous; the
  poll runs in a thread (`asyncio.to_thread`) while each ingest call is
  marshalled back onto the worker's event loop, so the database session
  and job-queue connector stay on their home loop.

### Provider setup

| Provider | Host | Notes |
| --- | --- | --- |
| Gmail | `imap.gmail.com` | Requires 2-Step Verification + an **app password** (myaccount.google.com → Security → App passwords); the account password will not work. IMAP must be enabled in Gmail settings. Folder names are labels — `Library/Processed` shows up as a nested label. |
| Fastmail | `imap.fastmail.com` | App password required (Settings → Privacy & Security → Integrations). |
| Outlook.com | `outlook.office365.com` | App password with 2FA; plain passwords are being phased out for IMAP. |
| Self-hosted (Dovecot etc.) | your host | Any account works; consider a dedicated `library@` address so the allowlist and folder layout stay clean. |

A dedicated mailbox (or a plus-address like `john+library@…` filtered
into its own folder set as `LIBRARY_EMAIL_FOLDER`) keeps the poller away
from personal mail. Example:

```sh
LIBRARY_EMAIL_HOST=imap.gmail.com
LIBRARY_EMAIL_USERNAME=library.intake@gmail.com
LIBRARY_EMAIL_PASSWORD=abcd efgh ijkl mnop   # Gmail app password
LIBRARY_EMAIL_ALLOWED_SENDERS=mthwsjc@gmail.com,partner@example.com
LIBRARY_EMAIL_POLL_MINUTES=10
```

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

Reads the `procrastinate_jobs` table directly: returns the most recent
jobs as
`[{id, status, task_name, attempts, scheduled_at, document_id}]`,
with `document_id` pulled from the job's JSON args when present.
`limit` defaults to 50 (max 500).

## Ingestion events

Append-only audit trail in `ingestion_events`:

| event | written by | detail |
| --- | --- | --- |
| `received` | ingest | `{filename, size, mime_type, source}`; email adds `{email_from, email_subject, email_message_id}` |
| `duplicate_upload` | ingest | `{filename, source}`; email adds the same `email_*` keys |
| `status_changed` | pipeline | `{from, to}` |
| `ocr_completed` | OCR stage | `{engine, confidence, pages, characters}`; plus `gate: {tesseract_confidence, rapidocr_confidence}` when the confidence gate retried |
| `ocr_failed` | OCR stage | `{error}` |
| `extraction_completed` | extraction stage | `{model, prompt_version, confidence, input_tokens, output_tokens, cost_usd, escalated, input_mode}` |
| `extraction_skipped` | extraction stage | `{reason, ...}` — `disabled`, `missing_api_key`, `budget`, `input_unusable`, `file_too_large` |
| `extraction_failed` | extraction stage | `{error, prompt_version}` |
| `failed` | pipeline | `{error, status}` |

## Configuration

Ingestion-related settings (the full environment reference, covering
every `LIBRARY_*` variable, is [`.env.example`](../.env.example)):

| Env var | Default | Used for |
| --- | --- | --- |
| `LIBRARY_DATA_DIR` | `/data` | Root of `originals/` and `derived/` |
| `LIBRARY_MAX_UPLOAD_BYTES` | `104857600` (100 MB) | Upload size cap |
| `LIBRARY_DATABASE_URL` | (see `config.py`) | Database + job queue (translated for psycopg) |
| `LIBRARY_OCR_LANGUAGES` | `nld+eng` | Tesseract language pack(s) (`-l` value) |
| `LIBRARY_OCR_CONFIDENCE_THRESHOLD` | `65.0` | Below this mean word confidence, retry via the photo path |
| `LIBRARY_TEXT_LAYER_MIN_CHARS_PER_PAGE` | `50` | Avg chars/page for a PDF's text layer to be usable (born-digital route when not scan-like; failure fallback otherwise) |
| `LIBRARY_ANTHROPIC_API_KEY` | unset | Anthropic API key; extraction is skipped when absent |
| `LIBRARY_EXTRACTION_ENABLED` | `true` | Master switch for the extraction stage |
| `LIBRARY_EXTRACTION_MODEL` | `claude-haiku-4-5` | Primary extraction model |
| `LIBRARY_EXTRACTION_ESCALATION_MODEL` | `claude-sonnet-4-6` | Retry model on low confidence / parse failure |
| `LIBRARY_EXTRACTION_DAILY_BUDGET_USD` | `5.0` | Estimated daily API spend cap; over budget → skip with `reason: "budget"` |
| `LIBRARY_CONSUME_DIR` | unset | Consume folder to watch; unset disables the watcher |
| `LIBRARY_CONSUME_FORCE_POLLING` | `false` | Poll instead of inotify — required for NFS/SMB-mounted consume dirs |
| `LIBRARY_CONSUME_POLL_INTERVAL_S` | `2.0` | Poll cadence when force-polling |
| `LIBRARY_CONSUME_STABILITY_S` | `3.0` | A file's size+mtime must be unchanged this long before ingest |
| `LIBRARY_CONSUME_ON_SUCCESS` | `archive` | `archive` → move to `consumed/YYYY/MM/`; `delete` → unlink |
| `LIBRARY_EMAIL_HOST` | unset | IMAP host; unset disables the email poller |
| `LIBRARY_EMAIL_PORT` | `993` | IMAP TLS port |
| `LIBRARY_EMAIL_USERNAME` | unset | Mailbox login |
| `LIBRARY_EMAIL_PASSWORD` | unset | Mailbox password (use an app password — see provider table) |
| `LIBRARY_EMAIL_FOLDER` | `INBOX` | Folder polled for new mail |
| `LIBRARY_EMAIL_PROCESSED_FOLDER` | `Library/Processed` | Where handled messages are moved (created if missing) |
| `LIBRARY_EMAIL_POLL_MINUTES` | `10` | Poll cadence (cron step; clamped to 1–59) |
| `LIBRARY_EMAIL_ALLOWED_SENDERS` | empty | Comma-separated sender allowlist; empty accepts anyone |
