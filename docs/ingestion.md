# Ingestion

How a file becomes a Document: upload → content-addressed storage →
database row → background job → status lifecycle. This document covers
storage layout, MIME handling, HEIC conversion, the upload API, the
Procrastinate job queue, the OCR stage (W4), the Claude metadata
extraction stage (W6), the consume folder watcher (W12), email-in
ingestion (W14), and in-app note authoring.

> **Authentication.** Every endpoint below requires a session cookie or
> bearer API token — see [api.md §1.9](api.md).

## Flow overview

Two stages: a synchronous **upload request** that stores the file and records a
row, then an asynchronous **worker pipeline** that processes it.

### Upload request — `POST /api/documents` (multipart)

The request handler runs these steps in order:

1. **Size check** — reject over `LIBRARY_MAX_UPLOAD_BYTES` (default 100 MB) with `413`.
2. **MIME detection** — sniff content, fall back to the client-declared type; `415` if unsupported.
3. **Hash** — compute `sha256(content)`.
4. **Duplicate check** — a non-deleted document with the same `sha256`? If so, log a `duplicate_upload` event and return `200 {duplicate: true}`.
5. **Store original** — atomic, idempotent write to `/data/originals/ab/cd/<sha256>`.
6. **HEIC/HEIF** — if needed, convert to JPEG at `/data/derived/ab/cd/<sha256>/converted.jpg`.
7. **Insert row** — `documents` row with `status=received` + a `received` ingestion event.
8. **Commit & defer** — commit, then defer `process_document(document_id)`.
9. **Respond** — `201 {id, sha256, status, duplicate: false}`.

### Worker pipeline — `process_document(document_id)`

Runs in the `worker` process (`python -m library.worker`). The document moves
through one status per stage, emitting one `ingestion_event` per transition:

`received → ocr → extract → markdown → embed → indexed`

Any error at any stage moves the document to `failed` and writes a `failed`
event with the error detail.

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
`image/heic`, `image/heif`, `image/tiff`, `text/plain`, `text/markdown`,
and Word `.docx`
(`application/vnd.openxmlformats-officedocument.wordprocessingml.document`,
`DOCX_MIME` in `library.docx`).

Detection (`library.ingest.detect_mime`) prefers content sniffing over
the client-declared type:

1. Sniff magic bytes with the **`filetype`** library (pure Python — no
   `libmagic` system dependency, unlike `python-magic`; it covers every
   binary type we accept, including HEIC).
2. `filetype` cannot identify text (it is magic-bytes based), so if
   sniffing fails and the content decodes as UTF-8, the type is
   `text/markdown` when the filename ends `.md`/`.markdown`, otherwise
   `text/plain`. (The binary sniff always wins over the filename.)
3. Otherwise fall back to the client-declared type (normalised, e.g.
   `image/jpg` → `image/jpeg`, `text/x-markdown` → `text/markdown`).

A `.docx` is a zip, and `filetype` returns the Office wordprocessing MIME
for it directly (the sniff is keyed off content, not the filename), so it
is detected the same way whatever channel it arrives on.

Anything that resolves outside the allowed set is rejected with **415**.
`.epub` and legacy `.doc` (`application/msword`) are **not** supported —
there is no route for them.

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

## DOCX handling (`library.docx`)

`docx_to_markdown(content: bytes) -> str`

Word `.docx` is converted to Markdown at ingest rather than given a bespoke
OCR route — Markdown is already a first-class type, so the conversion lets a
`.docx` flow through the existing extraction → chunk → search → viewer path.
The conversion is the "light path": `mammoth.convert_to_html` (semantic HTML
from the docx structure) → the shared `html_to_markdown` helper
(`library.markdown.html`: strip `script`/`style` via BeautifulSoup +
`markdownify` ATX). Headings, tables, lists, and free text survive; mammoth
warnings (e.g. unmapped styles) are non-fatal.

Storage mirrors HEIC: the **original `.docx` bytes** are what gets
content-addressed stored (so it stays downloadable and re-convertible), and
the Markdown is written as the derived artifact `converted.md` under
`derived_dir(sha256)`. Unlike HEIC, the document keeps its own
`mime_type` (`DOCX_MIME`), which is threaded through every MIME-routing
surface so the derived Markdown is what the pipeline actually consumes:

- **OCR router** (`library.ocr.router`): a `DOCX_MIME` branch reads
  `converted.md` (engine `docx`); if the derived file is missing (e.g. a
  re-process after the derived dir was cleaned) it re-converts the stored
  original.
- **Markdown layer** (`library.markdown.apply`): `DOCX_MIME` joins
  `text/markdown`/`text/plain` in the born-digital passthrough — the derived
  Markdown captured as `ocr_text` is written verbatim as one `DocumentPage`,
  with no Anthropic call.
- **Embedding** (`library.embedding.chunker.chunker_for_mime`): `DOCX_MIME`
  chunks with the structure-aware `chunk_markdown`, like `text/markdown`.
- **Extraction** and the **vision renderer** need no docx branch: extraction
  reads `ocr_text` (the Markdown) as text; the renderer is never reached
  because the passthrough handles docx first.

Conversion at ingest is **best-effort**: a corrupt `.docx` that still sniffs as
`DOCX_MIME` but that mammoth cannot parse does not fail the upload (which would
orphan the just-stored original) — the derived write is skipped and the document
is created anyway. The worker's OCR docx branch then re-converts from the stored
original, so a genuinely unreadable file surfaces as a normal `failed` document
(visible, retryable) rather than a 500. On the email channel the attachment is
instead recorded as a per-message skip (`email_ingest` catches per attachment).

The viewer has no inline preview for a `.docx` (it is neither image nor PDF);
the detail page shows the converted Markdown in the reader and offers a
**Download original** link for the stored `.docx`
(`GET /api/documents/{id}/original` serves it with its real MIME/filename).

## Ingestion service (`library.ingest`)

`ingest_file(session, *, content, filename, mime=None, source,
uploader_id=None) -> IngestResult(document, duplicate)` is the single
entry point used by the upload endpoint (and, later, by the consume
folder, email, and MCP channels — hence `source`).

Steps: detect/validate MIME → hash → dedup check → store original →
write the derived artifact for transform-at-ingest types (`converted.jpg`
for HEIC, `converted.md` for `.docx`) → create `Document`
(`status=received`, `source`, `original_filename`, `mime_type`) →
append a `received` ingestion event → **commit** → defer
`process_document(document_id)`.

Decisions:

- **Dedup:** an existing, non-deleted document with the same sha256 is
  returned with `duplicate=True`; a `duplicate_upload` ingestion event
  is recorded against it (with the attempted filename/source), and no
  file or row is written. **Notes are exempt** — they are authored, not
  uploaded, through the `/api/notes` router rather than `ingest_file`, and
  their `sha256` is a salted digest so identical/edited-back-to-identical
  bodies coexist (see "Notes" below).
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
  docker-compose). It processes `LIBRARY_WORKER_CONCURRENCY` documents at
  once (default 1 — serial). Raising it drains bursts faster but multiplies
  peak worker RAM (parallel OCR subprocesses + embedder batches), so only
  raise it with host headroom to spare.

### `process_document(document_id)` — pipeline

- Transitions: `received → ocr → extract → markdown → embed → indexed`, one
  commit and one `status_changed` ingestion event (`detail: {"from": …, "to": …}`)
  per transition. Entering `ocr` runs the OCR stage (below); entering `extract`
  runs Claude metadata extraction (see "Extraction"); entering `markdown` runs
  Claude vision markdown generation (see "Markdown layer"); entering `embed` chunks
  the text and stores bge-m3 vectors for semantic search (best-effort — a
  document that fails to embed still reaches `indexed`; see [ask.md](ask.md)).
- Stage hooks are `async def run_ocr(session, document)` / `run_extraction(session, document)` / `run_markdown(session, document)` / `run_embed(session, document)` in `library.jobs`. The OCR work
  itself is CPU-bound and subprocess-heavy, so it runs in a thread via
  `asyncio.to_thread`, keeping the async worker responsive.
- Re-runs are idempotent: the pipeline resumes from the document's
  current status; an already-`indexed` (or `failed`) document is left
  untouched.
- Any exception marks the document `failed`, records a `failed` event
  with `{"error": …, "status": <status it failed in>}`, and re-raises so
  Procrastinate also marks the job failed. A *clean* exception is therefore
  terminal and visible; there is no per-task retry policy (one would not help —
  a retried job hits the now-`failed`/terminal document and no-ops).
- **Crash recovery (stalled-job sweeper).** A *hard* kill (OOM, `SIGKILL`, host
  crash, or a redeploy that `SIGKILL`s the worker mid-stage) never runs the
  `except` block, so the document is left in a committed non-terminal status
  (`ocr`/`extract`/`markdown`/`embed`) and its Procrastinate job stays in
  `doing` with no re-queue — silently stranded. A periodic task
  `library.jobs.sweep_stalled_jobs` closes this: every
  `LIBRARY_STALLED_JOB_SWEEP_MINUTES` (default 5; `0` disables) it asks
  Procrastinate for `process_document` jobs whose worker heartbeat is older than
  `LIBRARY_STALLED_JOB_HEARTBEAT_SECONDS` (default 60 — safely above the ~10 s
  heartbeat period, so a live worker mid-OCR is never swept) and re-enqueues
  each via `retry_job`. Recovery is safe because the pipeline resumes
  idempotently from the stranded status. `get_stalled_jobs` can only see a
  stranded job while its dead worker's row still exists, and Procrastinate
  prunes those rows at worker startup — so the prune timeout is raised to 24 h
  via `LIBRARY_STALLED_WORKER_PRUNE_SECONDS`, otherwise a redeploy could delete
  the crashed worker's row before the sweep runs and hide the job permanently.

### `generate_thumbnail(document_id)` — first-page thumbnail (W7)

Deferred by the pipeline immediately after the OCR stage succeeds (it
needs nothing from extraction, so it runs in parallel with that stage).
Renders page 1 — pypdfium2 for PDFs, Pillow for images, HEIC via the
derived `converted.jpg` — to a ~480 px wide WebP at
`derived/<sha>/thumb.webp` and records a `thumbnail_generated` (or
`thumbnail_skipped`, e.g. for `text/plain`/`text/markdown`) event. The file's existence
is the only thumbnail marker; `GET /api/documents/{id}/thumbnail` serves
it (see [api.md](api.md)).

## OCR (`library.ocr`)

Tesseract alone is not good enough (see the inception decisions: great
on clean scans, collapses on phone photos), so OCR is a **router over
three engines**, selected by input type, with a confidence gate.

```
run_ocr(document, original_path, derived_dir) -> OcrResult
  │
  ├─ text/plain, text/markdown ► passthrough read           engine="text"
  ├─ DOCX_MIME (.docx) ────► read derived converted.md      engine="docx"
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
with Claude (decision 3 in the improvement plan): kind, sender, recipient,
title, summary, dates, total amount + currency, language, suggested tags. The
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
  on `claude-sonnet-4-6` ($3/$15 per MTok). When the primary ran on OCR text,
  this retry sends the **original file** (vision) rather than the same text —
  see "Input selection" below. The escalated result is used even if its
  confidence is also low (there is nothing better to do).

### Schema (`library.extraction.schema.ExtractedMetadata`)

| Field | Type | Notes |
| --- | --- | --- |
| `kind_slug` | enum | The 14 seeded kind slugs (`invoice` … `other`, including the general-reference kinds `reference`, `research`, `note`); enforced by the JSON schema, so the model cannot invent kinds. |
| `sender_name` | `str \| None` | Canonical short organisation/person name. The **signer** (sign-off) is the sender for letters/emails. |
| `recipient_name` | `str \| None` | The person the document is addressed **to**, in short canonical form (e.g. "John"). The salutation ("Dear/Beste/Geachte/T.a.v. …") is the primary signal. `None` only for impersonal material. |
| `addressee_raw`, `signer_raw` | `str \| None` | The **verbatim** salutation-target / sign-off names, copied as printed before canonicalisation. Additive (default `None`); feed the hybrid recipient path and the deterministic cross-checks. |
| `title`, `summary` | `str` | Always in **English** (translated from the source language if needed — prompt-enforced); summary ≤ 2 sentences (also prompt-enforced — string length constraints are not supported in structured-output schemas). |
| `document_date`, `due_date`, `expiry_date` | `date \| None` | Wire format is an ISO `YYYY-MM-DD` string; a before-validator trims it and maps empty/placeholder values to `None`, so sloppy-but-harmless output degrades to "no date" while a truly malformed date raises (and triggers escalation). The prompt and per-field descriptions define them distinctly: **document_date** = issue date; **due_date** = an act-by deadline (pay/respond/renew — Dutch "vervaldatum" on an invoice); **expiry_date** = a validity end ("geldig tot", "verloopt"). |
| `amount_total` | `str \| None` | Decimal string, parsed defensively (currency symbols stripped, `12,50` → `12.50`); unparseable values become `None` rather than failing the document. Converted to `Decimal` when applied. |
| `currency` | `str \| None` | ISO 4217; anything that is not three letters becomes `None`. |
| `language` | enum | `nld` / `eng` / `mixed` / `unknown`. |
| `tags` | `list[str]` | Normalised to lowercase slugs, deduplicated, capped at 8 client-side (array length constraints are also unsupported in the schema). |
| `topics` | `list[str]` | Human-readable topic phrases (kept as prose, **not** slugified) for general-reference material — what the document covers. Stripped, case-insensitively deduplicated, capped at 12. Empty (`[]`) for transactional paperwork. Persisted to the `documents.topics` JSONB column (migration 0010). |
| `confidence` | enum | `high` / `low` — `low` triggers escalation (the gate is binary). |
| `reasoning_note` | `str \| None` | One-line note when something needed judgement. |

Numeric min/max, string-length, and array-length constraints are not
supported by structured outputs, so everything of that shape is either
prompt-instructed or normalised by validators.

### Input selection

- **Normal case:** the user message is the document's `ocr_text`. Text up
  to `MAX_TEXT_CHARS` (8,000) is sent whole. **Longer text is sampled, not
  truncated** (`_sample_long_text`): `_SAMPLE_WINDOWS` (6) evenly-spaced
  windows — the first anchored at the start, the last at the end — are
  joined by a `[...]` marker, capped at `MAX_TEXT_CHARS_LONG` (24,000). This
  keeps head, middle, and tail of long general-reference material (manuals,
  papers, notes) in front of the model while spend stays bounded; the marker
  tells the model not to read across the gaps.
- **Garbage/empty OCR** (stripped text < 20 chars) and the original is
  a PDF or image: the original file is sent directly as a base64
  `document` (PDF) or `image` content block. HEIC/HEIF uses the derived
  `converted.jpg` (Claude does not accept HEIC). Files over 5 MB are
  not sent — extraction is skipped gracefully (`reason:
  "file_too_large"`). TIFF and text without usable OCR text are skipped
  with `reason: "input_unusable"`.
- **Vision escalation (thin-OCR recovery):** when the primary pass ran on OCR
  text and comes back **low-confidence**, the escalation re-attempts with the
  **original file** (`build_user_content(..., force_file=True)`) instead of
  re-sending the same text — an image PDF whose OCR captured only the letterhead
  (so the amount/recipient/date never reached the model) is then *read* by the
  escalation model off the page. It stays a single escalation call (2 total). If
  the original can't be sent (unusable mime, missing, or oversized) it falls back
  to the text escalation, so behavior never regresses; the escalated result's
  `input_mode` records what was actually sent (`document`/`image` vs `text`).

### Prompt

A versioned system prompt (`PROMPT_VERSION` in
`library.extraction.extractor`) describes Library, the kinds taxonomy, and
the Dutch+English domain. It now frames the archive as **two groups** —
transactional paperwork (invoices, receipts, …) and general reference
material (manuals, reference docs, research papers, notes) — and adapts the
instructions accordingly: a 2-sentence summary for paperwork but 3–6
sentences plus `topics` for general material, and an explicit note that a
missing sender/date/amount is normal for reference material and must **not**
be treated as a low-confidence signal (so clean general docs are not flagged
for review). It also gives explicit, contrasting guidance for the fields users
most often see blank or wrong: the **salutation** identifies the recipient and
the **sign-off** identifies the sender; **due_date** (an act-by deadline) is
distinguished from **expiry_date** (a validity end), including the Dutch
"vervaldatum" (due) vs "verloopt/geldig tot" (expiry) trap; and a short rubric
disambiguates overlapping `kind_slug` pairs (invoice vs receipt, utility-bill vs
invoice, letter vs contract, …). The same semantics are mirrored into per-field
`Field(description=…)` on the schema (Anthropic's structured-output guidance
recommends per-field descriptions). All free-text fields (title, summary,
reasoning_note) are **in English** (translated from the source language when the
document is e.g. Dutch), while the `language` field still records the *detected
source* language. The prompt version, model
and token usage of every run are stored on the document
(`extra["extraction"]`) and in the audit trail, so a future prompt
change can identify documents extracted with an older prompt.

### Applying results (`library.extraction.apply`)

On success:

- **Sender** is upserted by case-insensitive name match (create if new).
- **Recipient** resolution follows a **priority ladder**: the recipient named in
  the document itself is the overriding signal, and the contextual fallbacks only
  fire when the document names nobody. All tiers are **fill-only** and skipped
  once a user has edited `recipient_id`:
  1. **Document-stated recipient (rung 1).** The LLM's `recipient_name` (or the
     verbatim `addressee_raw` when it gave only that) is resolved by
     `match_existing_recipient` (`extraction/apply.py`): first against known users
     (by username / display name → that user's linked recipient), then against
     existing recipients by case-insensitive name. A match is always assigned. If
     the name matches **nothing**, a plain recipient is **created**
     (`upsert_recipient`) — but only when the extraction is **high-confidence**,
     so a garbled-OCR guess can't seed a junk row (it drops through instead). This
     is the "hybrid" model: a confident "Beste heer de Vries" becomes recipient
     "heer de Vries" even if that person was previously unknown.
  2. **Email `To:` (rung 2).** When the document named nobody usable,
     `resolve_recipient_from_email` (`extraction/apply.py`) matches the `To:`
     addresses on `extra["email_to"]` against users' `email_forward_addresses`
     (case-insensitive, via `match_user_by_email`) and sets that user's linked
     recipient. For forwarded mail the outer `To:` is the dropbox, so ingest also
     parses the **original** `To:`/`Aan:` from the forwarded body
     (`_forwarded_to_addresses` in `email_ingest`) and lists it **first**, so the
     earliest known recipient in the chain wins.
  3. **Owner (uploader) (rung 3).** If recipient is *still* unset and the document
     has an `uploader_id`, it is attributed to that owner's linked recipient
     (`get_or_create_user_recipient`). For email, `uploader_id` comes from the
     forwarder's `From:` (`resolve_sender_owner`); for the **consume folder** and
     **paperless importer** — which have no inherent uploader — it comes from the
     `import_default_owner` setting (unset = the document stays unowned and relies
     on rungs 1–2). A user's manual edit still wins over every rung.

  The **manual** edit path (`apply_document_update`, `PATCH /api/documents/{id}`)
  also uses `upsert_recipient`, unconditionally — a user assigning a recipient by
  hand always creates it if new.
- **Kind** is resolved by slug to the seeded `kinds` row.
- **Tags** are get-or-created by slug and merged into the document's
  tag set (never removed).
- Scalar fields (`title`, `summary`, dates, `amount_total`, `currency`,
  `language`) are set; `None` extraction values never null out existing
  data, and `language` is only set when not `unknown`.
- `topics` is written to `documents.topics` when the model returned any
  (and the field is not user-edited); like the other fields it is recorded
  in `fields_set`.
- `extra["extraction"]` records `{prompt_version, model, confidence,
  input_tokens, output_tokens, cost_usd, escalated, input_mode,
  fields_set, reasoning_note, addressee_raw, signer_raw}`. The last two are
  the verbatim salutation/sign-off names the model read; they power the
  deterministic recipient/sender cross-checks in `validation.py`.

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

### Backfill (stale prompt version)

`library backfill` re-enqueues the full `extract_document → markdown_document`
(→ embed) path for documents whose extraction `prompt_version` is **missing or
different** from the current `PROMPT_VERSION`, so an existing corpus picks up the
latest prompt, long-doc sampling, `topics`, and structure-preserving markdown
chunking.

Because re-extraction re-runs the current prompt, it is also how an existing
corpus picks up **recipient/sender/date** improvements: bumping `PROMPT_VERSION`
makes every older document "stale", and re-extraction now fills the
document-stated recipient (rung 1). To do the recipient-bearing kinds first
without re-paying for the whole archive, scope with `--kinds`:

```console
library backfill                              # general kinds on an old prompt version
library backfill --kinds letter,invoice,receipt   # recipient-bearing kinds first
library backfill --limit 100                  # throttle: first 100 matching ids only
library backfill --all-kinds                  # consider every kind, not just general
library backfill --include-current            # re-enqueue regardless of prompt version
library backfill --dry-run                    # count + scope only; enqueue nothing
```

- **`--general-only` (default) / `--all-kinds`.** By default only the
  general-reference kinds (`manual`, `reference`, `research`, `note`) are
  considered, so transactional documents (invoices, receipts, …) are **never
  re-paid for**. `--all-kinds` lifts that restriction.
- **`--kinds a,b,c`** restricts to the named kind slugs (overriding the
  general/all default) — e.g. `letter,invoice,receipt` to backfill
  recipient-bearing documents first for cost control. Pair with `--dry-run` to
  see counts before spending.
- **`--include-current`** re-enqueues documents already at the current prompt
  version too (e.g. to pick up a markdown-chunking change).
- **`--dry-run`** prints how many documents would be enqueued (with the kind +
  version scope) and enqueues nothing.

Like the other backfills it re-runs the same path new uploads use, so it honours
`extra["user_edited_fields"]` and the daily extraction budget — over-budget
documents are skipped worker-side (`extraction_skipped`, `reason: "budget"`) and
can be re-queued the next day. The CLI only enqueues; the worker must be running
to do the work.

### Backfill summaries

Documents ingested before extraction generated a summary have
`summary IS NULL`. `library backfill-summaries` enqueues an
`extract_document` task for each indexed, non-deleted document that still
lacks a summary, so they get one through the same path new uploads use —
under the current prompt that summary (and title) is in English regardless
of the document's language:

```console
library backfill-summaries              # all indexed docs with no summary
library backfill-summaries --limit 100  # first 100 only (throttle/budget)
```

Because it re-runs full extraction, it honours `extra["user_edited_fields"]`
and the daily extraction budget (`LIBRARY_EXTRACTION_DAILY_BUDGET_USD`):
documents beyond the budget are skipped with an `extraction_skipped`
(`reason: "budget"`) event and can be re-queued the next day. The worker
must be running to do the work; the command only enqueues the jobs.

## Extraction quality (`library.extraction.validation`, `library.extraction.judge`)

After the metadata is written, `apply_extraction` runs deterministic validation
and (separately) supports a batch eval harness for aggregate accuracy measurement.

### Validation rules

Pure, deterministic, zero API cost. `validate(document, ...)` returns a list of
`Finding(rule, field, severity, message)` dataclasses. All current findings have
`severity="warn"`.

| Rule | Field(s) | Fires when |
|---|---|---|
| `amount_grounding` | `amount_total` | Amount is set but its digit sequence is absent from `ocr_text` (normalised: strips currency symbols and separators, also checks the integer part). **Only fires when extraction read the OCR text** (`extra.extraction.input_mode == "text"`, or a legacy doc with no recorded mode): when the model read the page *image* instead (`input_mode` `document`/`image` — the vision fallback or born-unusable-OCR path), the amount was grounded in the image, so its absence from thin OCR is expected and the rule is suppressed. |
| `date_plausibility` | `document_date`, `due_date`, `expiry_date` | `document_date` is in the future or before 1990-01-01; or `due_date`/`expiry_date` is before `document_date`. When `document_date` is absent, a `due_date`/`expiry_date` before 1990-01-01 still fires ("implausibly old"). |
| `due_expiry_grounding` | `due_date`, `expiry_date` | A date is set but the OCR text shows **only** the opposite kind of cue word — bilingual (Dutch/English) sets in `validation.py`: due (`vervaldatum`, `te betalen voor`, `due by`, …) vs expiry (`geldig tot`, `verloopt`, `valid until`, …). Catches the model mislabeling a due date as expiry (classically a Dutch "vervaldatum") and vice-versa. |
| `amount_currency_coupling` | `currency` | Exactly one of amount/currency is set (the rule checks both fields; the finding's `field` attribute is `currency`) |
| `ocr_confidence_gate` | (document) | `ocr_confidence` is below `LIBRARY_EXTRACTION_VALIDATION_OCR_FLOOR` (default 50.0). **Suppressed when extraction read the page image** (`input_mode` `document`/`image`): the accepted result did not consume the OCR text, so its confidence says nothing about the extraction — same image-vs-text gate as `amount_grounding`. |
| `empty_extraction` | (document) | Kind is `other` or unset **and** no sender, no `document_date`, no `amount_total`, **and** no `title` and no `summary`. A clean general document (reference/research/note) that has a real title/summary but no sender/amount/date is therefore **not** flagged. |
| `missing_sender` | `sender_id` | `sender_id` is unset **and** either `amount_total` is set (a bill/receipt whose payee we couldn't identify) **or** the document is signed — a sign-off cue (`met vriendelijke groet`, `best regards`, …) or a stored `signer_raw` — but no sender was resolved. |
| `missing_recipient` | `recipient_id` | `recipient_id` is unset but the document appears to name an addressee — a stored `addressee_raw`, or a salutation cue (`dear`, `beste`, `geachte`, `t.a.v.`, …) in the OCR text. Surfaces the "a personally-addressed document has a recipient" expectation. |
| `missing_amount` | `amount_total` | `amount_total` is null but the OCR text carries a payment/due cue (`te betalen`, `vervaldatum`, `due by`, …) **and the kind is monetary** (invoice/receipt/utility-bill/quote). The classic thin-OCR image-PDF miss — the letterhead's payment line was OCR'd but the body's total was not. Scoped to monetary kinds so a passport's "vervaldatum" (expiry) doesn't spuriously flag. A safety net for the confident-but-wrong case the vision escalation (low-confidence only) does not catch. |
| `self_reported_low` | (document) | `extra["extraction"]["confidence"] == "low"`. The message carries the model's own one-line `reasoning_note` when present (e.g. "the extractor was unsure: two candidate totals on page 2"), falling back to a generic line otherwise. |
| `email_attachments_dropped` | (document) | The document came from an email whose *other* attachments could not be added (`extra["email_siblings_dropped"]` is non-empty). The message lists the dropped filenames. See "Email-in". |
| `email_item_ambiguous` | (document) | The email item was flagged `probably_noise` (or `ambiguous`) during selection — `extra["email_selection"]["verdict"]`, set by the optional LLM label pass. The message carries the flag's reason. See "Email item selection". |

**Cue-word grounding, not date-format parsing.** The recipient/sender/date
cross-checks (`due_expiry_grounding`, `missing_recipient`, `missing_sender`
sign-off branch) match bilingual **cue words** in the OCR text, not localized
date formats — the fiddly locale date-format matching is still out of scope.
Per-field and "multiple candidate value" confidence (e.g. "ambiguous sender")
are also out of scope — `ExtractedMetadata` carries one global `confidence` and
one value per field, so those reasons would need a schema change, not just a new
rule.

### `review_status` lifecycle

`documents.review_status` is a Postgres enum (`verified` / `needs_review` /
`unreviewed`), added in **migration 0006** (default `unreviewed`, indexed).

- Any finding ⇒ `needs_review`; no findings ⇒ `unreviewed`.
- User action via `POST /api/documents/{id}/verify` ⇒ `verified`.
- Re-extraction (or backfill) re-derives the status from fresh findings.

### `extra["validation"]` shape

Written by `_apply_validation` in `apply.py` as part of the extraction commit:

```json
{
  "prompt_version": "v3",
  "findings": [
    {"rule": "amount_grounding", "field": "amount_total", "severity": "warn",
     "message": "amount_total does not appear in the document text"}
  ],
  "validated_at": "2026-06-21T10:00:00Z"
}
```

`findings` is an empty list when no rules fired. The `validation` key on the
detail API response (`GET /api/documents/{id}`) exposes this blob directly.

### `extra["corrections"]` shape

Every field edited via `PATCH /api/documents/{id}` appends a record to
`extra["corrections"]` (the corrections flywheel). This is both a ground-truth
label source for the eval harness and a mining-ready shape for later few-shot
improvement:

```json
{
  "field": "amount_total",
  "original_value": "120.00",
  "corrected_value": "12.00",
  "source_excerpt": "…Totaal € 12,00…",
  "prompt_version": "v3",
  "model": "claude-haiku-4-5",
  "corrected_at": "2026-06-21T10:00:00Z"
}
```

`source_excerpt` is a best-effort ±40-character window from `ocr_text` around
the original value (empty string when not locatable — never blocks the edit).

### Backfill

To seed `review_status` for an existing corpus (the migration itself does not
backfill — fast and reversible):

```console
library backfill-validation              # all non-deleted documents
library backfill-validation --limit 100  # first 100 only (throttle)
```

Idempotent: re-running recomputes from current field values using the current
rule set. Useful after deploying new validation rules.

### Eval harness — `library eval-extractions`

Combines two ground-truth sources to produce per-field accuracy numbers:

- **Flywheel accuracy** — over every document with `extra["corrections"]`:
  fields the extraction got right vs. total fields set.
- **Judge agreement** — LLM-as-judge (`judge.py`) grades each sampled
  document's extracted fields against its OCR text, returning
  `correct`/`wrong`/`unsupported` per field. Runs on the configured judge model
  (default `claude-sonnet-4-6`). **Batch-only** — never called from the live
  pipeline.

Results are printed as a per-field table and persisted to the `eval_runs` table
(migration 0006) with `prompt_version`, `model`, `version_mix` (full
prompt/model distribution so a mixed-version sample is visible, not misleading),
`sample_size`, `per_field` (JSONB), and `overall`.

```console
library eval-extractions --sample 50   # judge 50 documents (deterministic head-slice)
library eval-extractions --all          # judge all documents with OCR text
```

Sampling is currently a deterministic head-slice (`eligible[:N]`). Random
seeded sampling is a documented follow-up.

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

**Budget-skip visibility & recovery.** A burst that exhausts the daily budget
leaves documents `indexed` but without LLM metadata. These are surfaced so they
are not lost silently: the admin **System** view reports
`stats.documents_budget_skipped` — the count of documents whose *latest*
extraction or markdown outcome was a budget skip (a later success clears it),
computed by `library.budget_backfill`. Recover them by running `library
backfill` (see "Backfill" above), or set `LIBRARY_BUDGET_BACKFILL_ENABLED=true`
to let the daily `backfill_budget_skipped` task (03:17 UTC, after the budget
resets) re-enqueue them automatically. The auto-backfill is **off by default**
because it spends money.

Every configured `*_model` knob (extraction, escalation, judge, markdown,
ask) must have a row in `MODEL_PRICING_USD_PER_MTOK`
(`library.extraction.pricing`); a `Settings` with an unpriced model fails
at startup rather than silently recording cost 0 and defeating the budget gate.

## Markdown layer (`library.markdown`)

The `markdown` pipeline stage renders each document page as clean
GitHub-flavored markdown using Claude vision, grounded on the OCR text.
This produces a structured, layout-aware representation: real tables
(including borderless/columnar tables reconstructed from the page image),
headings, lists, and emphasis. The markdown feeds the embed stage (richer
semantic retrieval) and the detail-view markdown tab; it also supplies
page provenance for Ask citations (see [ask.md](ask.md)).

**Full-text search reads the markdown layer too.** The concatenated per-page
markdown is denormalized onto `documents.pages_markdown` (a generated column
can only read same-row columns, so it cannot reach `document_pages`), and the
two generated FTS tsvector columns index `coalesce(pages_markdown, ocr_text)` —
preferring the markdown, falling back to raw OCR when a document has no pages
(migration `0025_fts_page_markdown`). So a thin-OCR image PDF is findable by
body text OCR never captured, and search snippets (`ts_headline`) are drawn
from the same source. `pages_markdown` is a search-only mirror kept in sync by
the app wherever page rows are written — `apply_markdown` (both the vision and
born-digital passthrough paths) and `api/notes.py` `_apply_body` — via
`markdown.apply.document_markdown_text`; `document_pages` remains the source of
truth for page content. Born-digital docs and notes have
`pages_markdown == ocr_text`, so the `coalesce` indexes the body once.

### Generation

Module `library.markdown` mirrors the shape of `library.extraction`:
`renderer.py` rasterizes pages, `schema.py` defines the structured-output
schema, `generator.py` calls the vision model, and `apply.py` handles
guards and persistence.

**Rasterization.** Pages are rasterized with pypdfium2 (the same library
used by thumbnails and the OCR confidence probe). The rendering scale is
`min(_PDF_RENDER_SCALE, long_side_px / long_side_pt)`, so a large-point
page never produces a bitmap much larger than the intended output — PDF
pages are rendered close to the target size, capped at the 2.0× upper
bound (~144 dpi). For Pillow-decoded images (JPEG/PNG/HEIC), a pixel-count
budget (~40 MP) is enforced before decoding; `DecompressionBombError` is
caught and treated identically — both result in `[]` (the document is
skipped with `reason: "input_unusable"`).

| Input type | Pages rendered |
|---|---|
| `application/pdf` | each page (capped at `LIBRARY_MARKDOWN_MAX_PAGES`) |
| `image/tiff` | the OCR-produced `searchable.pdf`'s pages |
| `image/jpeg`, `image/png` | the single image → one page |
| `image/heic`, `image/heif` | the derived `converted.jpg` → one page |
| `text/markdown`, `text/plain` | **no vision call** — born-digital text is its own markdown layer (see below) |

**Model call.** One `client.messages.parse()` call (async SDK, structured
outputs) per page-image batch, with content `[page images…] + [full
ocr_text as grounding text] + [instruction]` and schema `DocumentMarkdown
{pages: list[PageMarkdown]}` where `PageMarkdown = {page_number: int,
markdown: str}`. Pages are sent in batches of
`LIBRARY_MARKDOWN_PAGE_BATCH` (default 10); the per-batch results are
concatenated. `PROMPT_VERSION` is recorded with every run (currently
`"2026-06-21.1"`).

**Page numbering.** Returned pages are sorted by their reported
`page_number`, then re-assigned absolute positions (`offset + 1`,
`offset + 2`, …) clamped to the batch's image count. This means a
mis-numbered or short model response can never invent a page that has no
image.

**Coverage.** A batch yielding zero pages contributes nothing; if the
whole document yields zero pages, generation raises `MarkdownSkipped
("input_unusable")`. A document over `LIBRARY_MARKDOWN_MAX_PAGES` renders
only its first N pages (logged); pages beyond the cap have no markdown row
and their chunks fall back to `ocr_text`-style page-less embedding.

### `document_pages` storage (migration 0007)

Each rendered page is stored as one row in `document_pages`:

| Column | Type | Notes |
|---|---|---|
| `document_id` | FK `documents.id` ON DELETE CASCADE | |
| `page_number` | `int` | 1-based, part of PK |
| `markdown` | `text` | the page's rendered markdown |
| `char_count` | `int` | `len(markdown)` — cheap diagnostics |
| `created_at` | `timestamptz` | server default `now()` |

Primary key is `(document_id, page_number)`. The `Document.pages`
relationship uses `lazy="raise"` + `passive_deletes=True`, mirroring
`chunks` — per-page markdown is never wanted on a normal document load.
`apply_markdown` deletes and replaces a document's pages on each run,
making re-generation idempotent.

Migration 0007 also adds `document_chunks.page_number int | NULL`. `NULL`
means "no page provenance" — the document had no markdown layer (skipped,
failed, or text-only), or the chunk came from a page past the render cap.

### Pipeline stage (`markdown`) and best-effort contract

`run_markdown(session, document)` in `library.jobs` is the stage hook,
placed between `run_extraction` and `run_embed`. It calls
`apply_markdown(session, document, settings)` from `library.markdown.apply`.

**Born-digital text bypass.** For `text/markdown` and `text/plain` the raw
file content is already the authoritative text layer (captured verbatim as
`ocr_text` by the OCR passthrough), so `apply_markdown` short-circuits to
`_apply_born_digital_markdown`: it writes that body as a single
`DocumentPage` (page 1) with **no Anthropic call, no budget spend, and no
`markdown_max_pages` cap**, and records `markdown_completed` with
`{engine: "passthrough", model: null, pages: 1, cost_usd: 0.0}` (or
`markdown_skipped {reason: "no_text"}` for an empty body). Markdown documents
thus get a clean per-page layer (and page-aware embedding) for free; only
PDFs and images reach the vision model.

**Best-effort, identical contract to extraction.** For the remaining
(non-text) inputs, any of the following
causes `apply_markdown` to record a skip/failed event and return normally;
the document continues to `embed` and reaches `indexed`:

| Condition | Event |
|---|---|
| `LIBRARY_MARKDOWN_ENABLED=false` | `markdown_skipped` `{reason: "disabled"}` |
| No `LIBRARY_ANTHROPIC_API_KEY` | `markdown_skipped` `{reason: "missing_api_key"}` |
| Daily budget spent | `markdown_skipped` `{reason: "budget", spent_usd, budget_usd}` |
| Renderer returns no images (oversized, corrupt) | `markdown_skipped` `{reason: "input_unusable", mime \| error}` |
| Born-digital `text/markdown`/`text/plain` with content | `markdown_completed` `{engine: "passthrough", model: null, pages: 1, cost_usd: 0.0}` |
| Born-digital text with empty body | `markdown_skipped` `{reason: "no_text"}` |
| Generation yields no pages | `markdown_skipped` `{reason: "input_unusable", detail}` |
| API error after SDK retries | `markdown_failed` `{error, prompt_version}` |
| Success | `markdown_completed` `{model, prompt_version, pages, input_tokens, output_tokens, cost_usd}` |

The accepted limitation: a final DB-commit failure after the API call can
fail the document — the same edge case exists in extraction.

### Page-aware embedding

After the markdown stage, `run_embed` picks a chunker by MIME type and
checks for `document_pages`:

- **Chunker.** `text/markdown` documents use `chunk_markdown` (structure
  preserving — packs whole blank-line-delimited blocks, so headings, list
  items and tables survive; a single oversized block falls back to the
  word-packer). Every other type uses `chunk_text` (greedy word packing).
  Note this keys on the document's MIME type, so vision-generated markdown
  pages on a PDF are still chunked with `chunk_text`; only born-digital
  markdown gets `chunk_markdown`.
- **If pages exist:** chunk each page's markdown, carrying `chunk_index`
  continuously across pages, and tag every `DocumentChunk` with its
  `page_number`.
- **Else (no pages):** chunk `ocr_text`, with `page_number = NULL`.

The `embedded` event gains a `page_aware: bool` field so it is clear which
path ran.

### Backfill

```console
library backfill-markdown              # all documents without pages
library backfill-markdown --limit 100  # first 100 only (throttle)
library backfill-markdown --include-existing  # re-render all documents
```

Enqueues a `markdown_document` Procrastinate task per document. That task
runs `apply_markdown` then `run_embed` so the chunks are replaced from the
new pages. Idempotent; the worker must be running.

### `GET /api/documents/{id}/markdown`

Returns the assembled per-page markdown ordered by page number:
`{page_count: int, pages: [{page_number: int, markdown: str}]}`. Returns
an empty `pages` list (not 404) when the document has no markdown layer.
Backs the detail-view markdown tab.

## Consume folder (`library.consume`, W12)

A watched drop directory: anything placed there is ingested through the
same `ingest_file` service as an upload (`source=consume`). This is the primary
scanner flow — iOS Notes scan exports land in a Syncthing-synced folder that is
(or is mounted at) the consume dir. A consumed file has no inherent uploader, so
set `LIBRARY_IMPORT_DEFAULT_OWNER` to a username to attribute scans to an owner —
that lets the owner-as-recipient fallback (rung 3) fire when a scan states no
recipient of its own. Unset = the document stays unowned and relies on the
recipient named in the document itself.

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
  │   *.part; not under the configured consumed/failed archive
  │   dirs) ── no ─► ignored entirely
  ├─ stability wait: size+mtime must be unchanged for
  │   LIBRARY_CONSUME_STABILITY_S (re-sampled until stable, up to a
  │   5-minute cap) ── still changing at the cap ─► skipped; retried on
  │   the next filesystem event (or the next startup sweep)
  ├─ ingest_file(content, filename, source=consume)
  │    ├─ new document ─► row + "received" event + process_document job
  │    └─ duplicate    ─► "duplicate_upload" event on the existing row
  ├─ success (incl. duplicate) ─► move to {LIBRARY_CONSUMED_DIR}/YYYY/MM/
  │   (or unlink when LIBRARY_CONSUME_ON_SUCCESS=delete)
  └─ rejected (unsupported MIME, oversize, soft-deleted duplicate)
      ─► move to {LIBRARY_FAILED_DIR}/ + warning log
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
  and everything under the configured consumed/failed archive dirs
  (relevant when an override places an archive dir within the consume
  tree).
  Syncthing writes to a temp name and renames into place, so the
  finished file appears atomically as a single `added` event.
- **Duplicates are consumed.** A file whose content already exists gets
  a `duplicate_upload` event on the existing document and is archived
  like a success — the document is in the library, so the drop is done.
- **Failures.** Unsupported MIME, content over
  `LIBRARY_MAX_UPLOAD_BYTES`, or a soft-deleted-duplicate collision
  move the file to `{LIBRARY_FAILED_DIR}/` with a warning log. No
  ingestion event is written — these paths never create a document row,
  and `ingestion_events.document_id` is non-nullable, so the failed dir
  plus the log *is* the audit trail. Transient errors (database down,
  I/O) do **not** go to `{LIBRARY_FAILED_DIR}/`: the file stays in
  place and is retried on a later event or the next sweep.
- **Per-file isolation.** All per-file exceptions are caught and logged
  inside the watcher loop; nothing a dropped file does can take down
  the Procrastinate worker sharing the process.

### Archive layout

Successful files are moved to
`{LIBRARY_CONSUMED_DIR}/YYYY/MM/<original name>` — year/month of
consumption, so the archive stays browsable. Failures go to
`{LIBRARY_FAILED_DIR}/<original name>`. Both dirs default to
**siblings of the consume dir** (`<parent-of-consume-dir>/consumed` and
`<parent-of-consume-dir>/failed`) and are created at worker startup, so
the consume dir itself holds only pending items. Moves are EXDEV-safe:
`os.replace` on the same filesystem, with a copy-based fallback when
the archive dir sits on a different mount. Name collisions get a
numeric suffix (`scan-1.pdf`). Set `LIBRARY_CONSUME_ON_SUCCESS=delete`
to unlink instead of archiving.

Earlier releases kept both archive dirs *within* the consume dir
(`consumed/` and `failed/` directly under it). On startup the
worker runs a one-time migration: any legacy trees found there are
moved into the configured locations (collision-safe merge, EXDEV-safe)
and the emptied legacy dirs are removed. Subsequent startups are
no-ops.

### Supported extensions

`.pdf .jpg .jpeg .png .heic .heif .tif .tiff .txt .md .markdown`
(case-insensitive).
Anything else is ignored in place — extensionless or unknown files are
*not* moved to `{LIBRARY_FAILED_DIR}/`, because Syncthing folders routinely contain
foreign files (e.g. `.stfolder`) that are not ours to touch. Content
validation still happens at ingest: a `.pdf` that is not actually a PDF
is rejected by MIME sniffing and lands in `{LIBRARY_FAILED_DIR}/`.

### Syncthing / NAS notes

- The compose worker sets `LIBRARY_CONSUME_DIR=/data/consume` inside
  the shared `/data` volume. In production, bind-mount your
  Syncthing-synced folder over it (e.g.
  `- /srv/syncthing/scans:/data/consume`).
- **Archive dirs must live on a persistent mount.** The sibling
  defaults (`<parent-of-consume-dir>/consumed` and `…/failed`) are
  right when the consume dir sits inside a persistent volume (the
  compose default resolves to `/data/consumed` and `/data/failed`). But
  when the consume dir is its **own mount root** — as in production,
  where the NFS export is bind-mounted directly at `/consume` — the
  parent is the ephemeral container root and the archive would be lost
  on container replacement. Production must set
  `LIBRARY_CONSUMED_DIR=/data/consumed` and
  `LIBRARY_FAILED_DIR=/data/failed` explicitly (in the proxmox-setup
  compose) before deploying an image with this behavior.
- Archived files no longer sync back to the device that dropped them:
  the archive dirs sit outside the Syncthing-synced consume folder by
  design, so `/consume` holds only pending items.
- **inotify does not cross NFS/SMB.** If the consume dir is a network
  mount (NAS export, SMB share), file events never reach the container —
  set `LIBRARY_CONSUME_FORCE_POLLING=true` so watchfiles polls instead
  (`LIBRARY_CONSUME_POLL_INTERVAL_S` controls the poll cadence). A
  local bind mount synced by a Syncthing *on the same host* does not
  need polling.

## Email-in (`library.email_ingest`, W14)

A periodic Procrastinate task (`library.jobs.poll_email_inbox`) polls an
IMAP mailbox and ingests every supported attachment through the same
`ingest_file` service as an upload (`source=email`; the uploader is
resolved from the sender — see "Sender → owner attribution" below). When a
message has no ingestable attachment, the email body itself is ingested
instead (see "Body ingestion" below), so a mail that *is* the invoice works
too. The
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
       ├─ classify EVERY attachment (two-pass): sniff MIME, then the
       │   deterministic noise gate (inline signature images, tiny pixels/icons,
       │   calendar/vCard/PKCS7/TNEF parts ─► a quiet `filtered` drop); in the
       │   allowed set and within LIBRARY_MAX_UPLOAD_BYTES ─► an ingestable
       │   candidate; otherwise a recorded drop (oversize / unsupported_type)
       ├─ (optional) LLM label pass: one Anthropic call per email labels each
       │   surviving attachment keep|probably_noise; probably_noise ─► ingest
       │   AND flag needs_review (never dropped). OFF by default
       ├─ per candidate: ingest_file(source=email) — new document or
       │   `duplicate_upload`; a per-attachment error is recorded as a drop
       │   and the NEXT candidate still runs (one bad file never aborts siblings)
       ├─ every surviving document is stamped with the email's dropped siblings
       │   under extra["email_siblings_dropped"], so validation surfaces an
       │   `email_attachments_dropped` review reason ("N other attachments
       │   could not be added: …") — the files are never lost silently
       ├─ if no attachment produced a document ─► ingest the email BODY when it
       │   clears the substance gate (quoted replies / signatures / footers
       │   stripped; a contentless cover note like "FYI see attached" files
       │   nothing); HTML→Markdown (text/markdown), else plain text (text/plain)
       ├─ emit the one-line decision trace (one per processed message) and
       │   persist it as an `email_selection` event on each new document
       │   (see "Email item selection" below)
       ├─ any dropped attachment ─► a per-message WARNING summary and a
       │   best-effort "Attachments not added" push to the resolved owner
       │   (reuses the processing_error opt-in); quiet `filtered` noise is NOT
       │   surfaced (it never flags a real document)
       ├─ move the message to LIBRARY_EMAIL_PROCESSED_FOLDER (also for
       │   empty-bodied mails)
       └─ any per-message error ─► logged, message left in place for
           the next poll; the run continues with the next message
```

**Multiple attachments, only some relevant.** One email becomes **up to N
documents**, one per *useful* attachment. Every supported attachment is still
ingested by default — the loop is exhaustive, never "pick one" — but two gates
now remove the noise a forwarded mail carries (signature logos, tracking
pixels, calendar invites) and a third (optional) gate flags the ambiguous
middle. The full rules, config, and how to debug them live in **"Email item
selection"** immediately below. The guiding invariant is unchanged: **never
lose a real document.** A rejected-but-real file is still surfaced (recorded on
every sibling's `extra["email_siblings_dropped"]`, shown as the
`email_attachments_dropped` review reason, and pushed to the owner); only
unambiguous noise is dropped quietly (`filtered`), and even that is recorded in
the decision trace and recoverable from the IMAP `Processed` folder.

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
- **Sender → owner attribution.** Before ingesting, the poller resolves
  the sender address to a user (`resolve_sender_owner`): the document is
  owned by (`uploader_id`) the user whose
  `preferences.notifications.email_forward_addresses` contains the
  lowercased sender. On no match it falls back to the user named by
  `LIBRARY_EMAIL_DEFAULT_OWNER`, and on no fallback the document stays
  unowned (the pre-feature behaviour). Ownership is what routes the
  success/error **Pushover notification** to the right person — see
  [jobs-and-notifications.md](jobs-and-notifications.md) §1.5. Note: if
  you forward from your own mail client the `From:` header becomes *your*
  address, so list the addresses you forward *from* in your settings.
- **Recipient auto-fill from `To:`.** The message's `To:` address(es) are
  captured onto `document.extra["email_to"]` (`_to_addresses` / `_event_detail`
  in `email_ingest.py`, seeded into `Document.extra` via `ingest_file`'s
  `extra_document` parameter). This is **rung 2** of the recipient ladder (see
  "Applying results" above): it fills the recipient only when the document itself
  named nobody usable. On a **forwarded** message the outer `To:` is the library
  dropbox, so `_event_detail` also parses the **original** `To:`/`Aan:` header out
  of the forwarded body (`_forwarded_to_addresses`) and lists it **first**, so the
  person the mail was really sent to — the earliest known recipient in the chain —
  is chosen over the dropbox. Each address is matched against users'
  `email_forward_addresses` (case-insensitive) and, on a hit, sets that user's
  linked recipient. If none match, the **owner (uploader) fallback (rung 3)**
  attributes the document to whoever forwarded it (resolved from `From:`). Net
  effect for emails: the recipient comes from the document's own salutation first,
  else the earliest known email `To:` user, else the forwarding owner. It is
  **fill-only** (a document-stated recipient or a user's manual edit always wins)
  and reuses the same `email_forward_addresses` as sender→owner attribution.
- **Allowlist.** `LIBRARY_EMAIL_ALLOWED_SENDERS` is comma-separated and
  case-insensitive; empty (default) accepts mail from anyone, so set it
  whenever the address is guessable. Rejected mail stays in the inbox
  (it would otherwise vanish silently on a sender typo) — expect a
  warning log per poll until it is dealt with.
- **Body ingestion when there's no attachment.** When a message yields no
  attachment document — nothing attached, or nothing of a supported type —
  the email body itself is ingested as a document. The **HTML** body is
  preferred and **converted to Markdown** (stored as `text/markdown`) so
  invoice tables/formatting survive extraction, chunking (`chunk_markdown`),
  and the viewer, which renders Markdown; `script`/`style` subtrees are
  dropped first. When there is no HTML part, the **plain-text** body is stored
  as `text/plain`. (Storing raw `text/html` was rejected: the OCR router and
  markdown passthrough only handle `text/plain`/`text/markdown`, so a
  `text/html` document would fail extraction — see `run_ocr`.) The synthetic
  filename is the subject with a `.md`/`.txt` suffix (the suffix is what
  `detect_mime` reads to classify the body — see `_body_filename`). Body
  ingestion only fires when attachments produced *zero* documents, so an
  invoice PDF with a "see attached" cover note does not also spawn a body
  document. The body must also clear the **substance gate** (see "Email item
  selection" below): quoted replies, signatures, and mobile footers are
  stripped, and the remainder must reach `_BODY_MIN_WORDS`/`_BODY_MIN_CHARS`,
  so a contentless cover note ("FYI see attached") with no attachment files
  nothing. A genuinely empty-bodied mail also creates nothing and is filed away.
- **Sync IMAP off the worker loop.** imap-tools is synchronous; the
  poll runs in a thread (`asyncio.to_thread`) while each ingest call is
  marshalled back onto the worker's event loop, so the database session
  and job-queue connector stay on their home loop.

### Email item selection

A forwarded email is a list of candidates — `[body, attachment 1 … attachment N]`
— of which only some are worth filing. Selection decides each one's fate in three
layers, all in `library.email_ingest` (plus `library.email_label` for the LLM
layer). Overriding rule: **never lose a real document.** Nothing is deleted; an
item is either ingested, ingested-and-flagged, or recorded as a quiet/surfaced
drop, and the original mail always survives in the IMAP `Processed` folder.

**Where each item is decided:**

| Item | Deciding stage | Possible verdicts |
| --- | --- | --- |
| Attachment | `classify` (deterministic gate) | `ingested`, `duplicate`, `dropped` (oversize/unsupported/error — surfaced), `filtered` (signature_image / tiny_image / non_document_type — quiet) |
| Attachment | `llm_label` (optional, if enabled) | `flagged_ambiguous` (ingested **and** flagged needs_review), or falls through to `ingested` |
| Body | `body_substance` | `ingested`, `duplicate`, `dropped` (rejected), `filtered` (blank / below_substance / not_needed / oversize) |

**Layer 1 — deterministic noise gate** (`_noise_reason`, always on unless
`LIBRARY_EMAIL_FILTER_NOISE_ENABLED=false`). Runs in `_classify_attachments`,
*before* the allowed-type check, and drops only unambiguous noise:

- `signature_image` — an `image/*` part that is `Content-Disposition: inline`
  **or** whose `Content-ID` is referenced (`cid:…`) by the HTML body. This is
  the shape of a signature logo or embedded banner.
- `tiny_image` — an image whose longest edge ≤
  `LIBRARY_EMAIL_FILTER_TINY_IMAGE_MAX_EDGE_PX` (default 64), i.e. an icon or
  tracking pixel. Dimensions are authoritative (a small-but-normal-sized image
  is kept); byte size (`…MAX_BYTES`, default 4096) is only the fallback for an
  image that cannot be decoded. A decode failure never drops (bias to ingest).
- `non_document_type` — a part whose **declared** `Content-Type` is
  `text/calendar`, `text/vcard`/`text/x-vcard`,
  `application/(x-)pkcs7-signature`, or `application/ms-tnef`. The declared type
  is matched because calendar/vCard bytes sniff as the allowed `text/plain`, so
  they would otherwise be filed as junk text.

These drops are **quiet**: recorded in the decision trace and counted
(`attachments_filtered`) but not surfaced as `needs_review` and not pushed —
otherwise every footer logo would flag its real sibling. Thresholds are
deliberately conservative; when in doubt the gate keeps the file.

**Layer 2 — body substance gate** (`_body_substance` + `_body_candidate`). The
body is ingested only as a fallback (no attachment produced a document) *and*
only when it carries real prose: quoted-reply chains (`> …`, `On … wrote:`,
forwarded-message banners), signature blocks (`--`), and mobile footers ("Sent
from my iPhone") are stripped, and the remainder must reach `_BODY_MIN_WORDS`
(40) **or** `_BODY_MIN_CHARS` (240). The cleaned body is what gets filed, so a
document is never padded with a reply chain it merely quoted.

**Layer 3 — optional LLM label pass** (`library.email_label`, **OFF by
default**). When `LIBRARY_EMAIL_LABEL_ENABLED=true` and `LIBRARY_ANTHROPIC_API_KEY`
is set (the same key extraction uses), one Anthropic call per email
(`LIBRARY_EMAIL_LABEL_MODEL`, default
`claude-haiku-4-5`) labels each *surviving* attachment `keep` or
`probably_noise`, given the subject, sender, and a cleaned body excerpt
(`…BODY_SNIPPET_CHARS`). A `probably_noise` verdict **never drops** — the file
is ingested and its document is flagged `needs_review` via
`extra["email_selection"]`, which fires the `email_item_ambiguous` validation
finding. The pass is **fail-open**: a disabled feature, an exhausted daily
budget (`LIBRARY_EMAIL_LABEL_DAILY_BUDGET_USD`, default \$2, summed from
`email_label_completed` events like extraction), an API error, or a
malformed/incomplete response all keep every attachment. Spend is recorded as an
`email_label_completed` event on the first newly-produced document (an email that
files no *new* document — nothing filed, or only duplicates — has nowhere to hang
it, so its rare label spend goes uncounted: a deliberate under-count of at most
one cheap call per such email).

#### Debugging & triage: the decision trace

Every **processed** message emits exactly one **decision trace** log line — the
primary surface for "what happened to this email and why". It is emitted even
when the email produced no document (the persisted event cannot cover that
case), but two kinds of message never reach the trace:

- **Allowlist-rejected mail** — skipped before selection runs; each poll logs a
  per-message WARNING instead (`grep 'not in allowlist'`).
- **Messages whose processing raised** — the error is logged and the message is
  left in place for the next poll (`grep 'failed to process message'`).

Format (`_log_selection_trace`):

```
email-selection msg='<subject>' from='<sender>' items=N ingested=a duplicate=b \
  dropped=c filtered=d flagged=e :: <item> | <item> | …
```

Each `<item>` renders as `name:stage:verdict(reason)`, e.g.:

```
email-selection msg='Energy bill' from='biller@example.com' items=3 ingested=1 \
  duplicate=0 dropped=0 filtered=1 flagged=0 :: \
  bill.pdf:classify:ingested | logo.png:classify:filtered(signature_image) | \
  <body>:body_substance:filtered(not_needed)
```

Pull one email's trace from the logs (plain stdlib logging, so grep/Loki work):

```
# grep the worker logs
grep 'email-selection' worker.log | grep 'Energy bill'

# Loki (worker job label)
{job="library-worker"} |= "email-selection"
```

When a document **was** produced, the same per-item trace is also stored as an
`email_selection` `IngestionEvent` on it (visible in the document history's "Show
all events"), and the LLM billing as an `email_label_completed` event.

**What each verdict/reason means, and what to do:**

| verdict(reason) | Meaning | Action |
| --- | --- | --- |
| `ingested` | Filed as a document | none |
| `duplicate` | Content already in the library | none |
| `flagged_ambiguous` | Ingested, LLM thought it might be noise | check the needs-review queue; verify the doc |
| `filtered(signature_image\|tiny_image\|non_document_type)` | Deterministic noise, quietly dropped | none if truly noise; if a real doc was filtered, lower the threshold or set `LIBRARY_EMAIL_FILTER_NOISE_ENABLED=false` and re-forward |
| `filtered(below_substance:<n>w\|blank\|not_needed\|oversize)` | Body not filed (thin — `<n>w` is the post-strip word count, e.g. `below_substance:8w` / empty / an attachment already won / too large) | none |
| `dropped(oversize\|unsupported_type\|error)` | Surfaced attachment drop — also on the sibling's `email_siblings_dropped` + a push. (A body whose ingest is rejected renders as `dropped(rejected)`.) | investigate; re-send in a supported form if it was real |
| a trace with `flagged=0` while the LLM pass is on | The label pass was skipped (not a per-item verdict) | look for a `email-label: … skipped (budget\|error)` log line — raise the budget or accept degradation |

**Tuning knobs**: the noise-gate and label-pass settings
(`LIBRARY_EMAIL_FILTER_*`, `LIBRARY_EMAIL_LABEL_*`) are listed with their
defaults in the [Configuration](#configuration) table below.

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

## Notes (in-app authoring, `library.api.notes`)

A **note** is a born-digital `text/markdown` document composed inside Library
rather than uploaded — an additional ingestion channel (`source=note`) alongside
the existing sources (`upload`, `consume`, `email`, `api`, `mcp`, `import`).
Notes flow through the normal pipeline (one
`DocumentPage`, no OCR/vision call — the markdown body is its own text layer via
the born-digital passthrough; metadata is still auto-extracted), but they differ
from an upload in two ways: they are **edited in place** with a version history,
and they **bypass content dedup**.

The wire contract (POST / PATCH / versions / restore) is in
[api.md §1.17](api.md); this section covers the storage and processing
mechanics.

### Dedup bypass via a salted sha

Notes are authored through the `/api/notes` router, **not** `ingest_file`, so the
content-dedup check never runs for them. A note's `sha256` is a **salted digest**
— `sha256(body + uuid4())` — computed once at creation and fixed for the note's
life. The body is written **directly** to `path_for(sha256)` (bypassing
`storage.store`, which would re-hash the content and file it under a different
name). Two consequences:

- Two notes with identical bodies, or a note edited back to an earlier body,
  coexist as distinct documents instead of colliding on the unique `sha256`.
- Because the sha is fixed, an **in-place edit overwrites the same file** rather
  than creating a new content-addressed original.

### In-place edit, version history, re-processing

Because a note's body is born-digital, the **displayable layer is materialized
synchronously** on create/edit/restore — the on-disk file, `ocr_text`, and the
single `DocumentPage` (`page_count=1`) are written inside the request. The reader
is therefore correct the instant the call returns, independent of when (or
whether) the worker picks up the deferred job. The pipeline still runs
asynchronously for the *derived* layers only: metadata extraction and embeddings.

- **Create** (`POST /api/notes`): inserts the document
  (`status=received`, `extra.user_edited_fields=["title"]` so the title is locked
  against re-extraction), materializes the body (file + `ocr_text` + page),
  records a `received` event, and defers `process_document` — the same pipeline a
  new upload runs (re-OCR/markdown are idempotent no-ops over the materialized
  layer; it adds metadata + embeddings and advances to `indexed`).
- **Edit** (`PATCH /api/notes/{id}`): snapshots the note's *previous*
  `(title, body)` into a new `note_versions` row, overwrites the title and/or
  body in place (file + `ocr_text` + page rewritten synchronously), records a
  `note_edited` event, and — when the body changed — re-defers `extract_document`
  and `embed_document`. The current body for snapshotting is the document's
  `ocr_text` (the authoritative born-digital layer). A no-op edit (empty body)
  is a no-op: no snapshot, no event. 
- **Version history** (`GET /api/notes/{id}/versions`): every prior snapshot,
  newest first.
- **Restore** (`POST /api/notes/{id}/versions/{n}/restore`): snapshots the
  current state first (so a restore is undoable), then re-applies the chosen
  version's title + body, records a `note_restored` event, and re-processes.

`note_versions` (migration 0013) is append-only and mirrors `ingestion_events`:
`(id, document_id FK CASCADE, version_no, title, body, created_at)`, `version_no`
monotonic per document from 1. Migration 0013 also adds `'note'` to the
`document_source` CHECK constraint.

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
| `received` | ingest | `{filename, size, mime_type, source}`; email adds `{email_from, email_subject, email_message_id}`; a note records `{source: "note", size}` |
| `duplicate_upload` | ingest | `{filename, source}`; email adds the same `email_*` keys |
| `email_selection` | email poller | Per-item decision trace, persisted on each new document the email produced: `{email_from, email_subject, email_message_id, email_to?, items}` where each item is `{kind, filename, mime, size, stage, verdict, reason}` (see "Email item selection") |
| `email_label_completed` | email poller | `{cost_usd, model, input_tokens, output_tokens, prompt_version, items}` — one per billed LLM label pass, written on the first new document; what the label budget gate sums |
| `note_edited` | notes router | `{fields}` — the note fields changed by a `PATCH /api/notes/{id}` |
| `note_restored` | notes router | `{restored_version_no, restored_at}` |
| `status_changed` | pipeline | `{from, to}` |
| `ocr_completed` | OCR stage | `{engine, confidence, pages, characters}`; plus `gate: {tesseract_confidence, rapidocr_confidence}` when the confidence gate retried |
| `ocr_failed` | OCR stage | `{error}` |
| `extraction_completed` | extraction stage | `{model, prompt_version, confidence, input_tokens, output_tokens, cost_usd, escalated, input_mode}` |
| `extraction_skipped` | extraction stage | `{reason, ...}` — `disabled`, `missing_api_key`, `budget`, `input_unusable`, `file_too_large` |
| `extraction_failed` | extraction stage | `{error, prompt_version}` |
| `markdown_completed` | markdown stage | `{model, prompt_version, pages, input_tokens, output_tokens, cost_usd}`; born-digital text instead records `{engine: "passthrough", model: null, pages: 1, cost_usd: 0.0}` |
| `markdown_skipped` | markdown stage | `{reason, ...}` — `disabled`, `missing_api_key`, `budget`, `input_unusable`, `no_text` |
| `markdown_failed` | markdown stage | `{error, prompt_version}` |
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
| `LIBRARY_EXTRACTION_VALIDATION_OCR_FLOOR` | `50.0` | OCR-confidence threshold for the `ocr_confidence_gate` validation rule |
| `LIBRARY_EXTRACTION_JUDGE_MODEL` | `claude-sonnet-4-6` | Model used by `library eval-extractions` to judge extraction quality |
| `LIBRARY_EXTRACTION_JUDGE_INLINE` | `false` | Reserved; per-extraction judging hook (batch-only this phase) |
| `LIBRARY_MARKDOWN_ENABLED` | `true` | Master switch for the markdown stage |
| `LIBRARY_MARKDOWN_MODEL` | `claude-haiku-4-5` | Vision model for markdown generation |
| `LIBRARY_MARKDOWN_DAILY_BUDGET_USD` | `5.0` | Daily spend cap for markdown (independent of the extraction budget; over budget → skip with `reason: "budget"`) |
| `LIBRARY_MARKDOWN_MAX_PAGES` | `20` | Max pages rendered/sent per document |
| `LIBRARY_MARKDOWN_PAGE_BATCH` | `10` | Pages per vision call (batched with page-number offset) |
| `LIBRARY_MARKDOWN_IMAGE_LONG_SIDE_PX` | `1600` | Long-side cap for rendered page images sent to the model |
| `LIBRARY_WORKER_CONCURRENCY` | `1` | Documents the worker processes at once; raise only with RAM headroom |
| `LIBRARY_STALLED_JOB_SWEEP_MINUTES` | `5` | Cadence of the crash-recovery sweep; `0` disables it |
| `LIBRARY_STALLED_JOB_HEARTBEAT_SECONDS` | `60.0` | Worker-heartbeat age before an in-flight job is deemed stalled and re-enqueued |
| `LIBRARY_STALLED_WORKER_PRUNE_SECONDS` | `86400.0` | How long a dead worker's row survives before startup-prune; kept high so the sweep can still see stranded jobs after a redeploy |
| `LIBRARY_BUDGET_BACKFILL_ENABLED` | `false` | Daily 03:17 UTC re-enqueue of budget-skipped documents (spends; off by default) |
| `LIBRARY_CONSUME_DIR` | unset | Consume folder to watch; unset disables the watcher |
| `LIBRARY_CONSUMED_DIR` | `<parent-of-consume-dir>/consumed` | Archive dir for successful files (`YYYY/MM` subtree); created at worker startup. Default only applies when `LIBRARY_CONSUME_DIR` is set |
| `LIBRARY_FAILED_DIR` | `<parent-of-consume-dir>/failed` | Archive dir for rejected files; created at worker startup. Default only applies when `LIBRARY_CONSUME_DIR` is set |
| `LIBRARY_CONSUME_FORCE_POLLING` | `false` | Poll instead of inotify — required for NFS/SMB-mounted consume dirs |
| `LIBRARY_CONSUME_POLL_INTERVAL_S` | `2.0` | Poll cadence when force-polling |
| `LIBRARY_CONSUME_STABILITY_S` | `3.0` | A file's size+mtime must be unchanged this long before ingest |
| `LIBRARY_CONSUME_ON_SUCCESS` | `archive` | `archive` → move to `{LIBRARY_CONSUMED_DIR}/YYYY/MM/`; `delete` → unlink |
| `LIBRARY_IMPORT_DEFAULT_OWNER` | unset | Username that owns consume-folder and paperless-import documents (they have no inherent uploader); enables the owner-as-recipient fallback for them. Unset = unowned |
| `LIBRARY_EMAIL_HOST` | unset | IMAP host; unset disables the email poller |
| `LIBRARY_EMAIL_PORT` | `993` | IMAP TLS port |
| `LIBRARY_EMAIL_USERNAME` | unset | Mailbox login |
| `LIBRARY_EMAIL_PASSWORD` | unset | Mailbox password (use an app password — see provider table) |
| `LIBRARY_EMAIL_FOLDER` | `INBOX` | Folder polled for new mail |
| `LIBRARY_EMAIL_PROCESSED_FOLDER` | `Library/Processed` | Where handled messages are moved (created if missing) |
| `LIBRARY_EMAIL_POLL_MINUTES` | `10` | Poll cadence (cron step; clamped to 1–59) |
| `LIBRARY_EMAIL_ALLOWED_SENDERS` | empty | Comma-separated sender allowlist; empty accepts anyone |
| `LIBRARY_EMAIL_DEFAULT_OWNER` | unset | Username owning email documents whose sender matches no user's forwarding addresses; unset leaves them unowned |
| `LIBRARY_EMAIL_FILTER_NOISE_ENABLED` | `true` | Deterministic noise gate for email attachments (signature logos, tiny images, calendar/vCard/PKCS7/TNEF parts); `false` ingests every attachment as before |
| `LIBRARY_EMAIL_FILTER_TINY_IMAGE_MAX_BYTES` | `4096` | Byte-size fallback for the tiny-image filter, used only when an image cannot be decoded |
| `LIBRARY_EMAIL_FILTER_TINY_IMAGE_MAX_EDGE_PX` | `64` | Images whose longest edge is ≤ this are filtered as icons/tracking pixels |
| `LIBRARY_EMAIL_LABEL_ENABLED` | `false` | Optional per-email LLM label pass; flags probable noise `needs_review` (never drops). Spends money and needs `LIBRARY_ANTHROPIC_API_KEY` |
| `LIBRARY_EMAIL_LABEL_MODEL` | `claude-haiku-4-5` | Model used by the label pass |
| `LIBRARY_EMAIL_LABEL_DAILY_BUDGET_USD` | `2.0` | Daily spend cap for the label pass (summed from `email_label_completed` events); over budget → skip, all attachments kept |
| `LIBRARY_EMAIL_LABEL_BODY_SNIPPET_CHARS` | `1000` | Length of the cleaned body excerpt sent to the labeller |
