# Changelog

All notable changes to Library are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

**Markdown layer + page-aware citations** — Claude vision renders each
document page as clean GitHub-flavored markdown, grounded on OCR text.
See [docs/ingestion.md](docs/ingestion.md) "Markdown layer" and
[docs/ask.md](docs/ask.md).

- New pipeline stage `markdown` between `extract` and `embed`
  (`received → ocr → extract → markdown → embed → indexed`). Best-effort,
  identical contract to extraction: disabled, no API key, over budget,
  unusable input, or API error all skip gracefully; the document still reaches
  `indexed`.
- Per-page rendering via `client.messages.parse()` (structured outputs,
  `claude-haiku-4-5` default). Pages are rasterized with pypdfium2; scale is
  bounded to avoid oversized bitmaps from large-point-dimension PDFs; Pillow
  images are bounded by a ~40 MP pixel cap and `DecompressionBombError` is caught
  — corrupt uploads cannot fail the pipeline.
- New `document_pages` table (migration 0007, PK `(document_id, page_number)`)
  stores per-page markdown. `document_chunks` gains a nullable `page_number`
  column.
- Page-aware embedding: `run_embed` now chunks each page's markdown when
  `document_pages` exist, tagging every `DocumentChunk` with its `page_number`;
  falls back to `ocr_text` chunking (`page_number = NULL`) when absent. The
  `embedded` event gains `page_aware: bool`.
- Separate daily budget guard (`LIBRARY_MARKDOWN_DAILY_BUDGET_USD`, default
  $5.00 USD) scoped to `markdown_completed` events — independent of the
  extraction budget.
- `library backfill-markdown [--limit N] [--include-existing]` CLI to render
  existing documents and re-embed page-aware.
- `GET /api/documents/{id}/markdown` — assembled per-page markdown for the
  detail-view markdown tab.
- Ask citations now carry `page_number: int | None` (the page of the top
  semantic hit). The web UI renders `Title, p. N` and deep-links the PDF
  iframe via `#page=N` in the URL fragment. Aggregation citations (from
  `query_documents`) always have `page_number = None`.
- Six new `LIBRARY_MARKDOWN_*` settings (see `.env.example`).

**Semantic Ask** — natural-language question answering over the archive
(`/ask` in the web app, `POST /api/ask`). See [docs/ask.md](docs/ask.md).

- Local embeddings: a new `embedder` sidecar (text-embeddings-inference
  serving **bge-m3**, multilingual, 1024-dim) and a `document_chunks` table
  (pgvector + HNSW). Document text never leaves the host for indexing.
- New pipeline stage `embed` (`received → ocr → extract → embed → indexed`),
  best-effort: a document that fails to embed still indexes. Word-window
  chunking with overlap.
- Hybrid retrieval: bilingual Postgres full-text search fused with vector
  cosine k-NN via Reciprocal Rank Fusion.
- Structured query path over extracted columns (distinct senders, summed
  amounts by currency/sender/kind) for aggregation questions.
- `POST /api/ask`: a Claude tool-use loop that picks semantic vs structured
  retrieval and answers with citations; cost recorded per query in a new
  `ask_logs` table (not budget-gated this release).
- `library backfill-embeddings` CLI to index documents predating the embed
  stage (idempotent).
- Frontend Ask view with cited-answer cards and a sidebar link.

### Changed

- `db` image is now `pgvector/pgvector:pg17` (was `postgres:17.5-alpine`),
  initialised with `C.UTF-8` so text ordering stays byte-wise and an existing
  C-collation `pgdata` volume is reused safely. **The LXC now wants ~6–8 GB
  RAM** for the embedder — see [docs/deployment.md](docs/deployment.md) §1.7.1
  for the upgrade path.

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
