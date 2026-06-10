---
plan: library-greenfield-build
units:
  - id: W1
    title: Repo scaffold, GitHub repo, CI
  - id: W2
    title: Database schema, models, migrations
  - id: W3
    title: Storage service, ingestion core, job queue
  - id: W4
    title: OCR pipeline with routed engines
  - id: W5
    title: OCR benchmark on real iOS Notes scans
  - id: W6
    title: Claude metadata extraction
  - id: W7
    title: Documents REST API, search, thumbnails
  - id: W8
    title: Authentication and API tokens
  - id: W9
    title: Frontend foundation — GOV.UK design system port
  - id: W10
    title: Frontend — document list, search, upload
  - id: W11
    title: Frontend — document detail and metadata editing
  - id: W12
    title: Consume folder watcher
  - id: W13
    title: MCP server
  - id: W14
    title: Email-in ingestion
  - id: W15
    title: paperless-ngx importer
  - id: W16
    title: Mobile polish, PWA manifest, cross-device verification
  - id: W17
    title: Deployment hardening, docs completion, wrap
---

# 1. Improvement plan — Library greenfield build

**Run:** manual-20260610-154616. **Date:** 2026-06-10.
Basis: `evaluation-report.md` (requirements baseline R1–R7) and three
web-research reports (design system, backend stack, paperless-ngx API)
synthesized 2026-06-10.

## 1.1 Decisions and tradeoffs (research-grounded, June 2026)

1. **Job queue: Procrastinate** (v3.8.x, production-stable, Postgres
   LISTEN/NOTIFY). Rejected: arq (officially maintenance-only/dead),
   celery/taskiq/dramatiq (require a Redis/RabbitMQ container we don't
   need at tens of jobs/day). Compose stays api + worker + db; job
   status is a plain SQL query for the UI. Also drives periodic tasks
   (email poll).
2. **MCP: FastMCP 3.x** (jlowin, v3.4.x), streamable-HTTP transport
   (SSE is deprecated), mounted at `/mcp` inside the FastAPI app with
   shared lifespan; bearer auth reusing Library's API tokens. Rejected:
   official SDK's bundled FastMCP 1.0 (minimal), separate process
   (pointless at family scale).
3. **Extraction: Claude Haiku 4.5 via `client.messages.parse()`**
   (structured outputs GA; Pydantic schema; ~$0.0045/doc ≈ $4/month).
   Escalate per-document to Sonnet 4.6 on low confidence; send original
   image/PDF directly when OCR text is garbage; Batch API (-50%) for
   the paperless-ngx backfill only. Prompt caching skipped (below
   Haiku's 4096-token cacheable minimum).
4. **Auth: hand-rolled (~200 lines)** — pwdlib[argon2] (passlib is
   dead/broken on 3.13; FastAPI docs themselves moved to pwdlib),
   Postgres-backed httpOnly cookie sessions (revocable, no JWT), hashed
   opaque bearer tokens for REST/MCP automation. Rejected:
   fastapi-users (maintenance mode), authlib (no external IdP needed).
5. **Design system: govuk-frontend v6.2 SCSS** (MIT) consumed via Sass
   `@use`, with our own thin Vue SFC wrappers emitting GOV.UK markup.
   **GDS Transport font and crown imagery are licence-restricted to
   gov.uk — substituted with self-hosted Inter and a text-only
   masthead.** Rejected: govuk-vue (single maintainer, pinned to v5),
   from-scratch reimplementation (the SCSS *is* the design system).
6. **Frontend stack:** Vue 3.5 + Vite 8 (Rolldown) + vue-router 5 +
   Pinia 3 + Vitest 4, scaffolded with create-vue, TypeScript. Vapor
   mode ignored (still beta).
7. **OCR routing** (per inception decisions): pypdfium2 text-layer
   extraction → OCRmyPDF/Tesseract `nld+eng` for scans (primary path —
   iOS Notes exports) → OpenCV + RapidOCR (PP-OCRv5 latin, ONNX CPU)
   for raw photos; confidence-gated retry. pillow-heif for HEIC with
   `exif_transpose`.
8. **Camera capture:** `<input type="file" accept="image/*"
   capture="environment">` (zero-permission, reliable on iOS Safari);
   getUserMedia viewfinder explicitly out of scope for v1.
9. **paperless-ngx migration: REST API**, `Accept: application/json;
   version=9` (works on 2.x and 3.0), `download/?original=true` +
   MD5 verify, idempotency on paperless id + checksum. Rejected:
   document_exporter manifest (internal Django format, needs host
   shell, designed for paperless→paperless restore).
10. **Consume watcher: watchfiles** with `force_polling` config option
    (inotify doesn't fire across NFS/SMB bind mounts) and a
    file-stability check before ingest. **Email-in: imap-tools** (sync,
    inside a periodic Procrastinate task).
11. **Core pins:** FastAPI 0.136.x (lifespan, no on_event), SQLAlchemy
    2.0.50 async + asyncpg (`Mapped[]`/`mapped_column`), Alembic 1.18
    async template, Pydantic v2.

## 1.2 Non-goals (v1)

- No semantic/vector search (pgvector is a planned later phase; schema
  leaves room, no embeddings now).
- No per-user permissions or multiple libraries — one shared family
  library; accounts exist for login + attribution only.
- No OIDC/SSO (put Authelia in front later if wanted), no public
  internet hardening (rate limiting, fail2ban) — LAN/reverse-proxy use.
- No GPU/VLM OCR; no getUserMedia in-page scanner.
- No document editing/annotation, retention policies, or workflows.
- No native mobile apps — responsive web/PWA only.
- No UI internationalisation (UI is English; *documents* are nld+eng).
- No automated re-extraction of the whole corpus when prompts change
  (manual re-run command is in scope, bulk auto-migration is not).

## 1.3 Work units

Ordering rationale: strict foundation-first (W1–W3 unblock everything),
then the riskiest subsystem early (W4/W5 OCR — empirical risk R3) so
findings can still reshape the plan, then the data-value chain (W6–W8),
then UI (W9–W11), then independent ingestion channels and integrations
(W12–W15), polish and wrap last (W16–W17).

### 1.3.1 W1 — Repo scaffold, GitHub repo, CI

- **ID:** W1 · **Priority:** Critical · **Risk:** Low · **Size:** M
- **Changes:** git init; uv-managed Python 3.13 project (`pyproject.toml`,
  ruff config, pytest + coverage); create-vue scaffold under
  `frontend/` (TS, vue-router 5, Pinia 3, Vitest 4); `Dockerfile`
  (api/worker multi-stage) + `frontend` build stage; `docker-compose.yml`
  (api, worker, db: postgres:17; volumes for data + originals);
  `.github/workflows/ci.yml` (lint, pytest, vitest, build) and
  image push to `ghcr.io/johnmathews/library` on push to `main` with
  `GITHUB_TOKEN`, plus `workflow_dispatch:`; `docs/` skeleton
  (architecture.md, decisions snapshot), README; `gh repo create
  johnmathews/library --public`, push.
- **Test impact:** establishes both test harnesses; a trivial API
  healthcheck test and a frontend smoke test prove the harnesses run in
  CI. (No existing tests — greenfield.)
- **Reversibility:** pure additive; repo can be deleted.
- **Dependencies:** none.
- **Acceptance:** CI green on GitHub for push to main; `docker compose
  up` serves a `/healthz` 200 locally; image visible in ghcr.io.

### 1.3.2 W2 — Database schema, models, migrations

- **ID:** W2 · **Priority:** Critical · **Risk:** Medium (schema is
  foundational; later changes need migrations) · **Size:** M
- **Changes:** SQLAlchemy 2.0 async models + Alembic (async template):
  `users`, `sessions`, `api_tokens`, `documents` (sha256, mime, status
  lifecycle received→ocr→extract→indexed→failed, title, summary,
  document_date, language, sender FK, kind FK, amounts/expiry in
  typed columns where universal, `extra` JSONB for per-kind fields,
  `ocr_text`, `ocr_confidence`, `original_filename`, `page_count`,
  timestamps, uploader FK, source channel), `senders`, `kinds` (seeded:
  invoice, receipt, certificate, utility bill, parking ticket,
  warranty, manual, letter, contract, ticket, other), `tags` +
  `document_tags`, `ingestion_events` audit; generated tsvector columns
  (`dutch` + `english` configs) + GIN indexes; Procrastinate schema
  bootstrap.
- **Test impact:** new — model/migration round-trip tests against a
  real Postgres (testcontainers or compose service in CI).
- **Reversibility:** Alembic down-migrations for every revision.
- **Dependencies:** W1.
- **Acceptance:** `alembic upgrade head && alembic downgrade base &&
  alembic upgrade head` clean on empty Postgres; FTS index query
  matches a Dutch stem ("rekeningen" finds "rekening") in a test.

### 1.3.3 W3 — Storage service, ingestion core, job queue

- **ID:** W3 · **Priority:** Critical · **Risk:** Medium (persistent
  file layout is hard to change later) · **Size:** M
- **Changes:** content-addressed originals store
  (`/data/originals/ab/cd/<sha256>` + recorded original filename),
  dedup on hash with idempotent re-ingest; `POST /api/documents`
  multipart upload endpoint (accepts pdf/jpg/png/heic/tiff/txt);
  HEIC→JPEG conversion (pillow-heif, `exif_transpose`) preserving the
  original; Procrastinate wiring (worker container entrypoint) and a
  `process_document` task skeleton advancing the status lifecycle;
  `GET /api/jobs` status endpoint reading Procrastinate tables.
- **Test impact:** new — upload→stored→job-enqueued integration test;
  dedup test (same bytes twice → one document); HEIC fixture test.
- **Reversibility:** code revert; store layout documented before first
  real ingest.
- **Dependencies:** W2.
- **Acceptance:** uploading a fixture PDF returns 201 with document id;
  the file exists content-addressed on disk; a worker picks up the job
  and the document reaches a terminal status; duplicate upload returns
  the existing document.

### 1.3.4 W4 — OCR pipeline with routed engines

- **ID:** W4 · **Priority:** Critical · **Risk:** Medium · **Size:** L
  (kept whole: the router and engines only make sense together; the
  natural seam — benchmark-driven tuning — is already split into W5)
- **Changes:** `OcrEngine` protocol + router: pypdfium2 text-layer
  detection/extraction (born-digital); OCRmyPDF+Tesseract path
  (`-l nld+eng`, tessdata_best, `--rotate-pages --deskew --clean
  --oversample 300 --skip-text`) producing searchable PDF artifact +
  text + mean word confidence; photo path: OpenCV page-contour detect →
  4-point perspective transform → CLAHE → RapidOCR (PP-OCRv5 latin,
  onnxruntime CPU) with y,x reading-order sort; confidence gate
  (Tesseract below threshold → retry via RapidOCR, keep the better);
  Docker image gains tesseract-ocr + nld/eng tessdata + ocrmypdf +
  opencv-python-headless + rapidocr deps (worker image only).
- **Test impact:** new — unit tests per router branch with small
  fixtures (text-layer PDF, image-only PDF, photo JPEG); engine calls
  mocked in unit tests, one slow marked integration test running real
  Tesseract in CI.
- **Reversibility:** code revert; OCR outputs are stored derivations,
  re-runnable per document.
- **Dependencies:** W3.
- **Acceptance:** fixture image-only PDF yields non-empty `ocr_text`
  with recorded confidence and a searchable-PDF artifact; fixture photo
  routes through the RapidOCR path; born-digital PDF skips OCR
  entirely (asserted via instrumentation).

### 1.3.5 W5 — OCR benchmark on real iOS Notes scans

- **ID:** W5 · **Priority:** High · **Risk:** Low · **Size:** S
- **Changes:** `scripts/ocr_benchmark.py` — runs both engines over a
  sample dir, reports per-doc CER proxy (confidence, char counts,
  spot-check transcripts), timing, and a markdown report into
  `docs/benchmarks/`. **Needs ~10 real iOS Notes scan exports
  (Dutch + English mix) from John — the unit pauses for samples if none
  are present in `samples/` by the time it starts.** Tune thresholds
  (confidence gate, preprocessing toggles) from results.
- **Test impact:** none (script + report), deliberate.
- **Reversibility:** none needed.
- **Dependencies:** W4.
- **Acceptance:** committed benchmark report with per-path timings and
  the chosen confidence threshold justified by the data; router
  defaults updated to match.

### 1.3.6 W6 — Claude metadata extraction

- **ID:** W6 · **Priority:** Critical · **Risk:** Medium (API spend;
  failure handling) · **Size:** M
- **Changes:** `anthropic` SDK (async); Pydantic extraction schema
  (kind, sender, title, summary ≤2 sentences, document_date, total
  amount + currency, expiry/due date, language nld/eng/mixed, suggested
  tags, confidence); `client.messages.parse()` on `claude-haiku-4-5`,
  per-doc escalation to `claude-sonnet-4-6` when confidence low or
  parse empty; image/PDF direct input fallback when OCR text is
  garbage; idempotent `extract_metadata` task (re-runnable; stores
  model + prompt version per run); sender/kind/tag upsert with fuzzy
  sender matching; per-call cost logging to `ingestion_events`; clear
  failed-state handling — document stays searchable by OCR text.
- **Test impact:** new — schema validation tests; task tests with
  mocked Anthropic client (happy, low-confidence escalation, API-error
  retry); one optional live smoke test gated on `ANTHROPIC_API_KEY`.
- **Reversibility:** re-run extraction per document; metadata
  overwrites are versioned by `ingestion_events` audit trail.
- **Dependencies:** W4 (needs OCR text). Soft: W5 thresholds.
- **Acceptance:** fixture Dutch invoice (mock response) produces kind
  =invoice, sender created, date and amount populated; extraction
  failure leaves document in `indexed` state with metadata flagged
  missing, not `failed`.

### 1.3.7 W7 — Documents REST API, search, thumbnails

- **ID:** W7 · **Priority:** Critical · **Risk:** Low · **Size:** M
- **Changes:** versioned REST under `/api/`: document list with
  pagination + filters (kind, sender, tag, language, date range,
  status), FTS search (`websearch_to_tsquery` against dutch + english
  vectors, ranked, with snippets), document detail, metadata PATCH,
  original/searchable-PDF download, DELETE (soft delete); thumbnail
  generation task (pdfium first page render / Pillow for images,
  WebP) + `thumb` endpoint; OpenAPI tags/descriptions curated (the
  REST API is a first-class product surface for other tools).
- **Test impact:** new — API integration tests per endpoint incl.
  Dutch-stem search assertion and filter combinations.
- **Reversibility:** code revert; soft-deleted docs restorable.
- **Dependencies:** W2 (schema), W3 (files). Soft: W6 (richer fixtures).
- **Acceptance:** searching a Dutch stem returns the fixture doc with
  snippet; filters compose; downloads stream with correct content-type;
  OpenAPI docs render at `/docs`.

### 1.3.8 W8 — Authentication and API tokens

- **ID:** W8 · **Priority:** Critical · **Risk:** High (auth gates
  everything; mistakes are security bugs) · **Size:** M
- **Changes:** pwdlib[argon2] hashing; login/logout endpoints;
  Postgres-backed sessions (httpOnly, Secure, SameSite=Lax cookie,
  sliding expiry, revocation); CSRF token header for state-changing
  routes; hashed opaque API tokens (per-integration create/revoke/
  last-used, `Authorization: Bearer`); single FastAPI auth dependency
  accepting cookie or bearer; `library user add/passwd/disable` CLI
  (typer) for account management; all `/api/*` routes protected.
- **Test impact:** new — auth flow tests (login, wrong password, expired
  session, revoked token, CSRF rejection, bearer on MCP/REST paths);
  W3/W7 endpoint tests gain an authenticated client fixture.
- **Reversibility:** code revert; sessions/tokens tables dropped by
  down-migration.
- **Dependencies:** W2. Soft: do before UI (W9+) so the frontend is
  built against real auth.
- **Acceptance:** unauthenticated API calls 401; login sets httpOnly
  cookie and grants access; revoking a token immediately blocks it;
  Argon2 hashes verified in DB.

### 1.3.9 W9 — Frontend foundation: GOV.UK design system port

- **ID:** W9 · **Priority:** Critical · **Risk:** Low · **Size:** L
  (kept whole: the Sass setup, font substitution and base components
  are one coherent design-system bootstrap; screens are split out as
  W10/W11)
- **Changes:** govuk-frontend@6.2 via Sass `@use`; `$govuk-font-family`
  → self-hosted Inter (@fontsource/inter); **no GDS Transport, no
  crown** — text-only "Library" masthead; thin Vue SFC wrappers
  emitting documented GOV.UK markup: Button, Input, Textarea, Select,
  Radios/Checkboxes (conditional), ErrorSummary (focus-on-render),
  ErrorMessage, SummaryList, Tag, Pagination, NotificationBanner,
  ServiceNavigation, FileUpload (v6.2 enhanced, drop-zone), Panel,
  Details; per-component ES-module init in `onMounted`/unmount
  teardown; GOV.UK breakpoints (320/641/769/1280) and responsive type
  scale; login page using the error-summary pattern; authenticated app
  shell + router guards wired to W8 sessions.
- **Test impact:** new — Vitest component tests (markup classes, error
  summary focus behavior); Playwright smoke (login → shell) added to CI.
- **Reversibility:** pure frontend code.
- **Dependencies:** W1. Soft: W8 (login works end-to-end).
- **Acceptance:** login page renders GOV.UK-style at 320px and desktop
  widths with Inter; failed login shows error summary which receives
  focus; no GDS Transport/crown assets are served (asserted by a test
  scanning the build output).

### 1.3.10 W10 — Frontend: document list, search, upload

- **ID:** W10 · **Priority:** Critical · **Risk:** Low · **Size:** M
- **Changes:** document list (thumbnail, title, kind tag, sender, date;
  single column on mobile); search box + filter panel (kind, sender,
  tag, date) following GOV.UK form patterns; pagination; upload page
  with enhanced FileUpload (drag-drop on desktop) and
  `<input type="file" accept="image/*" capture="environment">` camera
  path on mobile; upload progress + processing-status polling (W3 jobs
  endpoint) with notification banner on completion; Pinia stores +
  typed API client.
- **Test impact:** new — component tests for list/filter logic;
  Playwright: upload fixture → appears in list.
- **Reversibility:** pure frontend code.
- **Dependencies:** W7, W9. Soft: W8.
- **Acceptance:** Playwright run uploads a fixture, sees it processed
  and listed, searches a Dutch stem and finds it; usable at iPhone
  viewport (375px) — verified by Playwright viewport test.

### 1.3.11 W11 — Frontend: document detail and metadata editing

- **ID:** W11 · **Priority:** High · **Risk:** Low · **Size:** M
- **Changes:** detail page — preview (image / PDF via pdf.js or
  searchable-PDF iframe), SummaryList metadata, inline edit forms
  (kind, sender, date, tags, title) with GOV.UK error patterns,
  re-run-extraction action, download original / searchable PDF, delete
  with confirmation page (GOV.UK pattern, no JS-only modal), OCR text
  view with search-term highlighting.
- **Test impact:** new — component tests for edit flows; Playwright
  detail-page pass.
- **Reversibility:** pure frontend code.
- **Dependencies:** W10.
- **Acceptance:** Playwright edits a document's kind and sender, sees
  persisted values after reload; delete requires confirmation;
  preview renders on iPad-width viewport.

### 1.3.12 W12 — Consume folder watcher

- **ID:** W12 · **Priority:** High · **Risk:** Low · **Size:** S
- **Changes:** watcher process in the worker container (watchfiles
  `awatch`, `LIBRARY_CONSUME_FORCE_POLLING` env for NFS/SMB mounts);
  file-stability wait (size unchanged N seconds) before ingest —
  iOS Notes/Syncthing copies arrive incrementally; success → archive
  to `consumed/` subdir (or delete per config), failure → `failed/`
  + ingestion_event; duplicate-safe via W3 dedup.
- **Test impact:** new — tmpdir integration test (drop file → document
  created; partial-write not ingested early).
- **Reversibility:** feature-flagged by env (`LIBRARY_CONSUME_DIR`
  unset = off).
- **Dependencies:** W3. Soft: W4 (full pipeline visible).
- **Acceptance:** dropping a PDF into the consume dir on the running
  compose stack yields a processed document without UI interaction;
  a partially-copied file is not ingested until complete.

### 1.3.13 W13 — MCP server

- **ID:** W13 · **Priority:** High · **Risk:** Low · **Size:** M
- **Changes:** FastMCP 3.x app mounted at `/mcp` (streamable HTTP,
  shared lifespan); bearer auth via W8 API tokens; tools:
  `search_documents` (query + filters → ranked results with metadata),
  `get_document` (metadata + OCR text), `get_document_file` (original
  or searchable PDF as resource/base64), `ingest_document`,
  `list_kinds`, `list_senders`, `list_tags`, `library_stats`; tool
  descriptions written for LLM consumers; docs page on connecting
  Claude/other clients.
- **Test impact:** new — MCP client integration tests (official client
  over streamable HTTP) for each tool incl. auth rejection.
- **Reversibility:** mounted sub-app, removable.
- **Dependencies:** W7 (search/service layer), W8 (tokens).
- **Acceptance:** an MCP client with a valid token lists tools, runs
  `search_documents` and retrieves a document; invalid token rejected.

### 1.3.14 W14 — Email-in ingestion

- **ID:** W14 · **Priority:** Medium · **Risk:** Low · **Size:** M
- **Changes:** periodic Procrastinate task (imap-tools, sync) polling a
  configured mailbox (env: host, user, password/app-password, folder,
  interval); ingests attachments (pdf/images) and optionally
  HTML-to-PDF for body-only mails (out: keep simple — attachments
  only in v1); idempotency by Message-ID + attachment hash; processed
  mail moved to a `Processed` folder; source recorded as `email` with
  sender hint passed to extraction.
- **Test impact:** new — poller tests against a mocked IMAP server
  (greenmail-style fixture or imap-tools mocks).
- **Reversibility:** feature-flagged by env (unset = off).
- **Dependencies:** W3. Soft: W6.
- **Acceptance:** test mailbox message with PDF attachment becomes a
  processed document with `source=email`; same message re-polled is
  not duplicated.

### 1.3.15 W15 — paperless-ngx importer

- **ID:** W15 · **Priority:** Medium · **Risk:** Medium (writes
  thousands of documents; must be idempotent) · **Size:** L (kept
  whole: fetch/map/verify is one transactional pipeline; splitting
  would create artificial seams)
- **Changes:** `library import paperless` CLI: REST API client pinned
  `Accept: application/json; version=9` (works on 2.x and 3.0); fetch
  taxonomies (tags/correspondents/document_types/custom_fields) and
  map → Library kinds/senders/tags (mapping table, `is_inbox_tag` →
  "needs-review" tag); per document: detail + `metadata/` +
  `download/?original=true`, MD5 verify, store via W3 (dedup-safe);
  carry over title, created (document_date), added, ASN, notes, custom
  fields (monetary "EUR123.45" parsing, documentlink remapped in a
  second pass, select via extra_data options); reuse paperless OCR
  `content` as initial ocr_text (skip re-OCR), queue Claude extraction
  via Batch API (50% discount) with `--no-extract` opt-out; skip
  trashed (`deleted_at`); resumable + idempotent on paperless id +
  checksum; dry-run mode with summary table.
- **Test impact:** new — importer tests against a mocked paperless API
  (respx fixtures for v9 payload shapes incl. null correspondent,
  missing archive version, custom-field types).
- **Reversibility:** imported docs carry `source=paperless-import` and
  an import batch id — a batch can be deleted wholesale; dry-run
  first.
- **Dependencies:** W3, W6, W7.
- **Acceptance:** dry-run against mock reports correct counts; live
  run twice imports each document exactly once; a fixture doc with
  null correspondent and no archive version imports cleanly.

### 1.3.16 W16 — Mobile polish, PWA manifest, cross-device verification

- **ID:** W16 · **Priority:** Medium · **Risk:** Low · **Size:** S
- **Changes:** web app manifest (icons, theme, `display` decided after
  on-device capture testing — research flags iOS standalone capture
  quirks; `browser` mode is acceptable fallback), apple-touch-icon;
  viewport/touch-target audit (≥44px) across screens; Playwright
  viewport matrix (iPhone/iPad/desktop) added to CI as the regression
  gate; documented on-device test checklist for John's iPhone
  (Add-to-Home-Screen, camera capture, upload).
- **Test impact:** extends Playwright suite with viewport matrix.
- **Reversibility:** pure frontend code.
- **Dependencies:** W10, W11.
- **Acceptance:** Playwright matrix green; manifest passes Lighthouse
  installability checks; checklist doc exists for the parts only a
  real device can verify.

### 1.3.17 W17 — Deployment hardening, docs completion, wrap

- **ID:** W17 · **Priority:** High · **Risk:** Low · **Size:** M
- **Changes:** production compose profile (restart policies, resource
  limits, healthchecks **using commands verified present in the slim
  images** — Python urllib, not curl; pg_isready for db), volumes/
  backup guidance (Postgres dump + originals dir, PBS-friendly), env
  reference (every LIBRARY_* var), reverse-proxy notes; complete
  `/docs` set: architecture, getting-started/deploy (LXC walkthrough),
  API guide, MCP guide, ingestion guide, migration guide, design-system
  notes (incl. font/crown licensing rationale); final coverage report;
  journal entry for the build; CHANGELOG + v0.1.0 tag.
- **Test impact:** CI gains a compose-up smoke job (healthchecks reach
  healthy).
- **Reversibility:** docs/config only.
- **Dependencies:** all prior (final unit).
- **Acceptance:** fresh-clone deploy following only the docs reaches a
  working login on a clean machine; all healthchecks healthy; docs
  list no stubs or placeholders; coverage reported in CI summary.

## 1.4 Coverage of evaluation findings

R1 (font/crown licensing) → W9. R2 (scope sequencing) → ordering + W10
core path early, W14/W15 late. R3 (OCR empirics) → W5. R4 (extraction
idempotency/cost) → W6. R5 (queue choice) → decision 1, W3. R6 (MCP
churn) → decision 2, W13. R7 (paperless format) → decision 9, W15. All
inception requirements map to units; nothing out of scope except the
items in Non-goals.
