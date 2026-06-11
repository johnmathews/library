# Changelog

All notable changes to Library are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [0.1.0] — 2026-06-11

First release: a complete, deployable self-hosted document archive.

### Added

**Ingestion**
- Content-addressed storage (SHA-256, atomic writes, dedup); HEIC→JPEG
  conversion with originals preserved; MIME sniffing with a strict
  allowed set (PDF, JPEG, PNG, HEIC/HEIF, TIFF, plain text).
- Web upload (multi-file, progress, duplicate detection), watched consume
  folder (Syncthing-safe stability checks, archive/delete policy,
  NFS/SMB polling mode), email-in via IMAP polling (sender allowlist,
  processed-folder idempotency), REST upload, and MCP ingestion.
- paperless-ngx importer (`library import paperless`): idempotent,
  resumable, dry-run mode, MD5-verified originals, full metadata mapping
  (correspondents, types incl. Dutch synonyms, tags, monetary custom
  fields, document links), batch escape hatch.
- Append-only ingestion audit trail per document.

**Processing pipeline**
- Procrastinate (Postgres-native) job queue; `received → ocr → extract →
  indexed` lifecycle with per-stage events and idempotent re-runs.
- Routed OCR: text-layer extraction for born-digital PDFs (scan-aware:
  iOS-Notes-style scans with embedded text are still re-OCRed),
  OCRmyPDF/Tesseract `nld+eng` with searchable-PDF artifacts, OpenCV +
  RapidOCR (PP-OCRv5) for photos, and a confidence gate that retries
  weak Tesseract results on the neural path (validated by a real-corpus
  benchmark, `docs/benchmarks/`).
- Claude metadata extraction via structured outputs: `claude-haiku-4-5`
  escalating once to `claude-sonnet-4-6` on low confidence; kind, sender,
  title, summary, dates, amount/currency, language, tags; daily budget
  cap, cost/provenance recorded per document, user-edited fields never
  overwritten; graceful skip without an API key.
- First-page WebP thumbnails.

**Search and API**
- Postgres FTS in Dutch and English simultaneously (dual tsvector
  columns, websearch syntax, ranked snippets via `ts_headline`).
- REST API under `/api`: documents CRUD + soft delete, search/filters/
  pagination, downloads (original, searchable PDF, thumbnail), taxonomy
  endpoints, job visibility, re-extraction; OpenAPI docs at `/docs`.
- Authentication: Argon2id passwords, Postgres-backed sliding sessions
  (httpOnly cookie + CSRF double-submit), per-integration revocable
  bearer API tokens; `library user` admin CLI.
- MCP server at `/mcp` (FastMCP, streamable HTTP, bearer tokens):
  search, read, file retrieval, ingestion, taxonomy and stats tools.

**Web app**
- Vue 3 + TypeScript SPA styled on GOV.UK Design System code (MIT) with
  licence-restricted GDS assets (Transport typeface, crown/crest)
  replaced by self-hosted Inter and a text masthead — enforced by a
  build-time asset check.
- Document list with full-text search, filter panel, URL-synced state,
  pagination, snippets; detail view with browser-native PDF preview,
  inline metadata editing, re-extraction, OCR-text view with highlight;
  upload with per-file progress and pipeline polling; delete
  confirmation page; XSS-safe snippet rendering.
- Mobile/PWA: installable manifest (`minimal-ui`), monogram icons, safe
  areas, ≥44px touch targets, no horizontal scroll at 320px; Playwright
  e2e matrix (desktop Chromium, iPhone WebKit, iPad WebKit).

**Deployment and operations**
- Single multi-stage Docker image (Python 3.13 slim + OCR system
  packages + built frontend); the API process serves the SPA with
  immutable asset caching and an `index.html` fallback.
- Production-shaped `docker-compose.yml`: pinned Postgres 17.5, restart
  policies, healthchecks on all three services, memory limits, automatic
  Alembic migrations via a one-shot service, `.env` support with a fully
  documented `.env.example`.
- Documentation set under `docs/`: architecture, deployment (Proxmox
  LXC walkthrough, reverse proxy, backups, upgrades, troubleshooting),
  API, MCP, ingestion, frontend, paperless migration, OCR benchmark.
- CI: backend lint+tests with coverage, frontend lint/type/unit/build +
  licence asset check, Playwright e2e against the real stack, a compose
  smoke job (boot → healthy → login), and image publishing to
  `ghcr.io/johnmathews/library`.

[0.1.0]: https://github.com/johnmathews/library/releases/tag/v0.1.0
