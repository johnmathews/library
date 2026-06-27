# 1. Architecture

**Status:** active. **Last updated:** 2026-06-28.

Library is a self-hosted personal/family document archive. This document
describes the system design and tracks which parts exist. The full
decision record (with research and rejected alternatives) lives in
`.engineering-team/runs/manual-20260610-154616/` and the development
journal in `journal/`.

## 1.1 System overview

Long-running services — `api`, `worker`, a Postgres (`db`) and a local
`embedder` sidecar — plus a one-shot `migrate` job, over one shared data
volume:

```
                ┌────────────────────────────────────────┐
 iOS Notes ──┐  │ worker: OCR, Claude extraction,        │
 scanner   ──┼─▶│ consume-folder watcher, email poller   │──┐
 email     ──┘  │ (Procrastinate jobs)                   │  │
                └────────────────────────────────────────┘  │
                                                            ▼
 Vue SPA ─────▶ ┌────────────────────────────────────────┐ ┌──────────────┐
 REST clients ▶ │ api: FastAPI + mounted MCP server      │ │ PostgreSQL 17 │
 MCP clients ─▶ │ (/api, /mcp, /healthz)                 │▶│ + FTS (nl/en) │
                └────────────────────────────────────────┘ │ + job queue   │
                            │                              └──────────────┘
                            ▼
                /data: content-addressed originals + derived artifacts
```

- **api** — FastAPI app. Serves the REST API under `/api`, the MCP server
  mounted at `/mcp` (FastMCP, streamable HTTP), and `/healthz`. In
  production it also serves the built Vue frontend (baked into the image;
  hashed assets cached immutable, SPA fallback to `index.html` — see
  [deployment.md](deployment.md) §1.3). In dev the Vite server proxies
  `/api` instead.

  > **Naming note.** This service is deployed as **`library-webserver`**
  > on the home server (matching the `paperless-webserver` convention of
  > that stack). There is no separate frontend container and no separate
  > MCP container: the web app, REST API and MCP server are one process
  > on one port. "Webserver" describes the role — the HTTP-facing half
  > of the app, beside `worker` and `db` — not what it speaks.
- **worker** — same image, different entrypoint: a Procrastinate worker
  consuming jobs from Postgres (OCR, metadata extraction, thumbnails),
  plus the consume-folder watcher and periodic email poll. No Redis —
  Procrastinate queues jobs in Postgres via LISTEN/NOTIFY.
- **db** — PostgreSQL 17 with **pgvector**. Documents metadata, full-text
  search (generated tsvector columns in both `dutch` and `english` configs),
  chunk embeddings (`document_chunks`, HNSW), users/sessions/API tokens, and
  the job queue.
- **embedder** — a local [text-embeddings-inference](https://github.com/huggingface/text-embeddings-inference)
  sidecar serving **bge-m3** (multilingual, 1024-dim) over HTTP. Used by the
  `worker` (indexing) and the `api` (query-time `/ask`). Document text never
  leaves the host for embedding. See [ask.md](ask.md).

## 1.2 Document pipeline

Every ingested file follows the same lifecycle, recorded on the document
row: `received → ocr → extract → markdown → embed → indexed` (or `failed`
at any stage, with the reason in `ingestion_events`).

1. **Ingest** (any channel: web upload, consume folder, email, REST, MCP).
   The file is hashed (SHA-256) and stored content-addressed under
   `/data/originals/ab/cd/<sha256>`; duplicate content is detected by hash
   and not re-ingested. HEIC is converted to JPEG (original kept).
2. **OCR** — routed by input type:
   - Born-digital `text/plain`/`text/markdown` → passthrough read, no OCR
     (the file content becomes the text layer directly).
   - PDF with a text layer → direct extraction (pypdfium2), no OCR.
   - Scans / image-only PDFs (the primary path — iOS Notes scan exports
     land here) → OCRmyPDF + Tesseract `nld+eng` with deskew/clean/
     oversample; also produces a searchable-PDF artifact.
   - Raw photos → OpenCV perspective correction + RapidOCR (PP-OCRv5
     latin model, CPU). One model covers Dutch and English together.
   - Confidence gate: a low-confidence Tesseract result is retried via
     the neural path and the better result kept.
3. **Extract** — Claude (Haiku 4.5, structured outputs via
   `messages.parse()`) turns OCR text into metadata: kind, sender, title,
   summary, document date, amounts, expiry, language, suggested tags, and —
   for general-reference material — `topics`. The prompt spans both
   transactional paperwork and general material (manuals, reference docs,
   research papers, notes); long documents are **window-sampled** (head +
   evenly-spaced middles + tail, joined by `[...]`) rather than truncated, so
   signal from a whole manual reaches the model within a fixed token budget.
   Low-confidence documents escalate to Sonnet 4.6. Extraction is
   idempotent and re-runnable; a document whose extraction fails stays
   searchable by its OCR text. As the final step inside `apply_extraction`,
   deterministic validation rules run against the extracted values and OCR
   text: they set `review_status` (`verified`/`needs_review`/`unreviewed`)
   on the document row and write findings to `extra["validation"]`. A batch
   eval harness (`library eval-extractions`) combines the corrections
   flywheel with an LLM-as-judge to produce per-field accuracy numbers;
   see [ingestion.md](ingestion.md) "Extraction quality".
4. **Markdown** — Claude vision (Haiku 4.5 by default) rasterizes each
   page and renders a clean GitHub-flavored markdown representation,
   grounded on the OCR text. One `messages.parse()` call per page-image
   batch; results are stored per page in `document_pages`. **Born-digital
   `text/markdown`/`text/plain` bypass the vision model entirely**: the raw
   content is already the text layer, so it is written as a single
   `document_pages` row with no API call or budget spend. Best-effort,
   exactly like extraction: disabled feature, missing API key, blown
   budget, unusable input, or an API error all produce a skip/failed event
   and the document continues to `embed`. See
   [ingestion.md](ingestion.md) "Markdown layer".
5. **Embed** — the document's text is chunked and embedded (bge-m3, 1024-dim)
   by the local `embedder` sidecar; vectors land in `document_chunks` (HNSW
   index) for semantic retrieval. When `document_pages` exist the embed
   stage chunks each page's markdown and tags every chunk with its
   `page_number`; otherwise it falls back to `ocr_text` with
   `page_number = NULL`. Best-effort: a document that fails to embed still
   reaches `indexed` and stays searchable by full-text. See [ask.md](ask.md).
6. **Index** — metadata and text become searchable (Postgres FTS, both
   Dutch and English stemming; semantic retrieval via `document_chunks`) and
   visible in the UI.

## 1.3 Data model (summary)

`documents` (hash, mime, lifecycle status, `review_status` enum, title,
summary, document_date, language, amounts/expiry, `topics` JSONB list of
human-readable subject phrases for general-reference material, `extra` JSONB for
kind-specific fields plus `extra["validation"]` + `extra["corrections"]`,
OCR text + confidence, uploader, source channel) with FKs to `senders` and `kinds`
(seeded: invoice, receipt, certificate, utility bill, parking ticket,
warranty, manual, reference, research, note, letter, contract, ticket, other —
the last group of general-reference kinds added in migration 0010 alongside
`topics`), many-to-many `tags`, many-to-many `projects` (first-class
collections — `projects` + `document_projects`, migration 0011; soft-archived
via `archived_at`, documents survive a project delete),
per-page markdown renderings (`document_pages`, PK `(document_id, page_number)`),
per-chunk embeddings (`document_chunks`, pgvector + HNSW; each chunk carries
`page_number` when generated from `document_pages`, `NULL` when falling back to
`ocr_text`), append-only `ingestion_events` audit trail, Ask conversation
persistence (`ask_threads` — one conversation per owner; `ask_turns` — one
Q&A turn per thread, storing cost/provenance and the serialized Anthropic
message blocks used to replay prior tool results into follow-up questions),
cached per-series prose descriptions (`series_insights`, one row per
`(sender_id, kind_id, currency)` — see [ask.md §1.7](ask.md)), and
auth tables (`users`, `sessions`, `api_tokens`). Originals on disk are
immutable; everything else (including embeddings and page markdown) is a
re-derivable artifact.

## 1.4 Interfaces

- **REST API** (`/api`) — versioned, cookie- or bearer-authenticated,
  OpenAPI-documented. The full product surface: search, CRUD, downloads,
  ingestion, job status, and natural-language **Ask** (`POST /api/ask`, see
  [ask.md](ask.md)).
- **MCP server** (`/mcp`) — FastMCP over streamable HTTP, bearer tokens.
  Tools for searching, reading, and ingesting documents from LLM clients.
- **Web app** — Vue 3 SPA following GOV.UK design principles (content
  first, responsive 320px-up, accessible). Typeface is self-hosted Inter:
  GDS Transport and the crown are licence-restricted to gov.uk services.

### 1.4.1 Live job events

Document processing runs in the **worker**, but the UI lives in the **api**
process. They are bridged by Postgres `LISTEN/NOTIFY`: as a document moves
through the pipeline, `library.jobs.notify_document_event` emits a `NOTIFY` on
the `library_doc_events` channel (best-effort — a notify failure never strands a
document). The api process exposes a Server-Sent Events endpoint
(`GET /api/events`, `library.api.events`) that holds a dedicated `LISTEN`
connection and relays each notification to connected browsers.

On the frontend, a Pinia `jobs` store opens one `EventSource`, tracks in-flight
documents (driving the navbar running-jobs indicator and the live `/jobs` view),
and raises a toast when a document reaches `indexed` or `failed`. The flow is
strictly one-way (server→client), which is why SSE is used rather than a
WebSocket. See [api.md](api.md) §1.8.5 and
[jobs-and-notifications.md](jobs-and-notifications.md).

## 1.5 Authentication

Named family accounts over one shared library. Browser: Argon2 password
hashing (pwdlib), Postgres-backed sessions in an httpOnly cookie.
Automation (REST/MCP): per-integration opaque bearer tokens, stored
hashed, individually revocable.

## 1.6 Implementation status

| Area | Unit | Status |
|------|------|--------|
| Scaffold, CI, Docker skeleton | W1 | **done** |
| DB schema + migrations | W2 | **done** |
| Storage + ingestion + queue | W3 | **done** — see [ingestion.md](ingestion.md) |
| OCR pipeline | W4 | **done** — see [ingestion.md](ingestion.md), "OCR" section |
| OCR benchmark (real samples) | W5 | **done** — see [benchmarks/260610-ocr-benchmark.md](benchmarks/260610-ocr-benchmark.md); scan-aware routing + gate fix landed |
| Claude metadata extraction | W6 | **done** — see [ingestion.md](ingestion.md), "Extraction" section |
| REST API + search + thumbnails | W7 | **done** — see [api.md](api.md) |
| Auth | W8 | **done** — see [api.md](api.md) §1.9 |
| Frontend foundation (design system) | W9 | **done** — see [frontend.md](frontend.md) |
| Frontend: list, search, upload | W10 | **done** — see [frontend.md](frontend.md) §1.4–1.6; Playwright e2e in CI |
| Frontend: document detail + editing | W11 | **done** — see [frontend.md](frontend.md) §1.4.2; added `GET /api/kinds\|senders\|tags` + `POST /api/documents/{id}/extract` ([api.md](api.md) §1.8.2, §1.8.4) |
| Consume watcher | W12 | **done** — see [ingestion.md](ingestion.md), "Consume folder" section |
| MCP server | W13 | **done** — see [mcp.md](mcp.md) |
| Email-in | W14 | **done** — see [ingestion.md](ingestion.md), "Email-in" section |
| paperless-ngx importer | W15 | **done** — see [migration.md](migration.md) |
| Mobile/PWA polish | W16 | **done** — see [frontend.md](frontend.md) §1.8 (manifest + monogram icons, safe areas, ≥44px touch targets, 3-project Playwright matrix, on-device checklist) |
| Deployment hardening + full docs | W17 | **done** — see [deployment.md](deployment.md); compose smoke job in CI; v0.1.0 ([CHANGELOG](../CHANGELOG.md)) |
| Semantic Ask (pgvector, embedder, hybrid retrieval, `/api/ask`) | — | **done** — see [ask.md](ask.md) |
| Extraction quality (validation, review queue, eval harness) | — | **done** — see [ingestion.md](ingestion.md) "Extraction quality" and [api.md](api.md) §1.3/1.4/1.8.3 |
| Markdown layer (vision per-page rendering, page-aware embed, page citations in Ask) | — | **done** — see [ingestion.md](ingestion.md) "Markdown layer" and [ask.md](ask.md) |
| Conversational Ask (multi-turn threads, history replay, prompt caching, chat UI) | — | **done** — see [ask.md](ask.md) §1.6 and [api.md](api.md) §1.11–1.12 |
