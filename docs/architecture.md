# 1. Architecture

**Status:** active. **Last updated:** 2026-06-11.

Library is a self-hosted personal/family document archive. This document
describes the system design and tracks which parts exist. The full
decision record (with research and rejected alternatives) lives in
`.engineering-team/runs/manual-20260610-154616/` and the development
journal in `journal/`.

## 1.1 System overview

Three containers, one Postgres database, one shared data volume:

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
- **db** — PostgreSQL 17. Documents metadata, full-text search (generated
  tsvector columns in both `dutch` and `english` configs), users/sessions/
  API tokens, and the job queue.

## 1.2 Document pipeline

Every ingested file follows the same lifecycle, recorded on the document
row: `received → ocr → extract → indexed` (or `failed` at any stage, with
the reason in `ingestion_events`).

1. **Ingest** (any channel: web upload, consume folder, email, REST, MCP).
   The file is hashed (SHA-256) and stored content-addressed under
   `/data/originals/ab/cd/<sha256>`; duplicate content is detected by hash
   and not re-ingested. HEIC is converted to JPEG (original kept).
2. **OCR** — routed by input type:
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
   summary, document date, amounts, expiry, language, suggested tags.
   Low-confidence documents escalate to Sonnet 4.6. Extraction is
   idempotent and re-runnable; a document whose extraction fails stays
   searchable by its OCR text.
4. **Index** — metadata and text become searchable (Postgres FTS, both
   Dutch and English stemming) and visible in the UI.

## 1.3 Data model (summary)

`documents` (hash, mime, lifecycle status, title, summary, document_date,
language, amounts/expiry, `extra` JSONB for kind-specific fields, OCR text
+ confidence, uploader, source channel) with FKs to `senders` and `kinds`
(seeded: invoice, receipt, certificate, utility bill, parking ticket,
warranty, manual, letter, contract, ticket, other), many-to-many `tags`,
append-only `ingestion_events` audit trail, and auth tables (`users`,
`sessions`, `api_tokens`). Originals on disk are immutable; everything
else is a re-derivable artifact.

## 1.4 Interfaces

- **REST API** (`/api`) — versioned, cookie- or bearer-authenticated,
  OpenAPI-documented. The full product surface: search, CRUD, downloads,
  ingestion, job status.
- **MCP server** (`/mcp`) — FastMCP over streamable HTTP, bearer tokens.
  Tools for searching, reading, and ingesting documents from LLM clients.
- **Web app** — Vue 3 SPA following GOV.UK design principles (content
  first, responsive 320px-up, accessible). Typeface is self-hosted Inter:
  GDS Transport and the crown are licence-restricted to gov.uk services.

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
| Frontend: document detail + editing | W11 | **done** — see [frontend.md](frontend.md) §1.4.2; added `GET /api/kinds\|senders\|tags` + `POST /api/documents/{id}/extract` ([api.md](api.md) §1.8.1–1.8.2) |
| Consume watcher | W12 | **done** — see [ingestion.md](ingestion.md), "Consume folder" section |
| MCP server | W13 | **done** — see [mcp.md](mcp.md) |
| Email-in | W14 | **done** — see [ingestion.md](ingestion.md), "Email-in" section |
| paperless-ngx importer | W15 | **done** — see [migration.md](migration.md) |
| Mobile/PWA polish | W16 | **done** — see [frontend.md](frontend.md) §1.8 (manifest + monogram icons, safe areas, ≥44px touch targets, 3-project Playwright matrix, on-device checklist) |
| Deployment hardening + full docs | W17 | **done** — see [deployment.md](deployment.md); compose smoke job in CI; v0.1.0 ([CHANGELOG](../CHANGELOG.md)) |
