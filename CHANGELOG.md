# Changelog

All notable changes to Library are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

**Consume archive dirs are now siblings of the consume folder** — successful
and rejected files move to `LIBRARY_CONSUMED_DIR` / `LIBRARY_FAILED_DIR`
(new env vars, defaulting to `<parent-of-consume-dir>/consumed` and
`<parent-of-consume-dir>/failed` when `LIBRARY_CONSUME_DIR` is set) instead of
`consumed/` and `failed/` *inside* the consume dir, so the consume folder holds
only pending items. Both dirs are created at worker startup, moves work across
mounts (EXDEV-safe), and a one-time startup migration relocates any legacy
in-consume archive trees to the new locations (collision-safe merge). Archived
files therefore **no longer sync back** to the device that dropped them via the
Syncthing-shared consume folder — deliberately dropped in favour of the cleaner
layout. **Operator action (production):** the consume dir is its own NFS mount
at `/consume`, so the sibling default would land on the ephemeral container
root — set `LIBRARY_CONSUMED_DIR=/data/consumed` and
`LIBRARY_FAILED_DIR=/data/failed` on the worker in the proxmox-setup compose
**before** pulling this image. See [docs/ingestion.md](docs/ingestion.md)
"Archive layout" and [docs/deployment.md](docs/deployment.md) §1.7.2.

### Fixed

**Email triage flags are visible without extraction** — the two email review
reasons (`email_item_ambiguous`, `email_attachments_dropped`) used to be
computed only by the extraction stage, so a flagged document stayed
`unreviewed` whenever extraction was disabled, budget-skipped, or failed.
They now run **at ingest** (a pure `email_findings` pass over `Document.extra`
when the document is created), so the `needs_review` badge and its reason
appear immediately; a later extraction re-derives the status without losing
the flag. See [docs/ingestion.md](docs/ingestion.md) "Validation rules".

**Email poller robustness** — four hardening fixes to the IMAP poll:
a socket timeout (`LIBRARY_EMAIL_IMAP_TIMEOUT_SECONDS`, default 60 s) bounds
every IMAP operation and a wedged/dead server now aborts the poll with a
warning instead of hanging the worker forever; the periodic poll task is
**overlap-guarded** (Procrastinate `queueing_lock` + `lock`, so a slow poll
can never pile up concurrent runs); the "attachments couldn't be added" push
fires only **after** a message's successful move to the Processed folder, so
it is at-most-once per message across retries; and the LLM label pass's spend
event anchors on the **first produced document — new or duplicate** — so an
all-duplicate re-send still counts against the daily label budget.

**Email attachments are no longer dropped silently** — when a forwarded email
carried several files and only some were ingestable (e.g. three PDFs and a
photo), the rejected files used to vanish with only a container log line: no
record, no notification, no review flag. Now every dropped attachment is
recorded on each surviving document (`extra["email_siblings_dropped"]`),
surfaced as an `email_attachments_dropped` review reason ("N other attachments
could not be added: …"), and pushed to the owner ("Attachments not added",
reusing the `processing_error` opt-in). A per-attachment failure is also
isolated — one bad file can no longer abort its siblings or wedge the message
into a permanent retry. See [docs/ingestion.md](docs/ingestion.md) "Email-in".

### Added

**Email hold-for-review + whole-email LLM verdict** — an inbound email the
pipeline judges *not library-worthy* is now **held for review** instead of
silently processed or left in the inbox: a durable `held_emails` row
(**migration 0026**) plus a move to the Held IMAP folder
(`LIBRARY_EMAIL_HELD_FOLDER`, default `Library/Held`). Four triggers hold —
the LLM label pass's new **whole-email verdict** (`email-label-v2` now judges
the message as a whole, body included, so body-only newsletters are caught;
fail-open: any label failure files exactly as before), a thin body-only mail,
an email whose drops left nothing ingested, and an allowlist-unknown sender —
all behind a master switch (`LIBRARY_EMAIL_HOLD_ENABLED`, default on;
`false` is the rollback lever). Held emails surface in a new **`/held-emails`
queue** (web view + REST API: list/detail/**ingest anyway**/**dismiss** —
dismiss keeps the record and the bytes forever) with a dashboard affordance
and an opt-in `email_held` push. *Held* is deliberately distinct from
`needs_review`: no document exists while held. See
[docs/ingestion.md](docs/ingestion.md) "Held for review",
[docs/api.md](docs/api.md) §1.21, and the
[email-triage runbook](docs/runbooks/email-triage.md). **Operator action
(production):** migration 0026 applies automatically on deploy (the `migrate`
service runs `alembic upgrade head`); set `LIBRARY_EMAIL_LABEL_ENABLED=true`
(the hold switches default on; `LIBRARY_ANTHROPIC_API_KEY` is already set
where extraction runs), and restart the **worker** container — the enablement
recipe is in the runbook §7.

**More specific "needs review" reasons** — "extraction was unsure" now carries
the model's own one-line reason (its `reasoning_note`, e.g. "the extractor was
unsure: two candidate totals on page 2") instead of a fixed generic string, and
two new rules name concrete problems: `missing_sender` (a bill/receipt whose
payee couldn't be identified) and `email_attachments_dropped` (files from the
same email that couldn't be added). See [docs/ingestion.md](docs/ingestion.md)
"Validation rules".

**Charts view (`/charts`)** — a new aggregate **charts dashboard** in the web
app: a responsive grid of bar-chart tiles, one per eligible recurring
`(sender, kind)` series, each showing the trend, its cached LLM description, and
an editable "documents in this series" list. A shared control bar drives every
tile (time range, custom datepickers, and a group-by that buckets and sums
amounts per calendar period). Supports **authored/manual series** creation,
single-chart pages (`/charts/{id}`) with **PDF / JPEG / PNG export** and
copy-link, and smart panels (signature-matching suggestions + deterministic,
grounded odd-ones-out). Reachable from the sidebar **Charts** link. See
[docs/frontend.md](docs/frontend.md) §1.7, [docs/ask.md](docs/ask.md) §1.7, and
[docs/api.md](docs/api.md) §1.14.

**Dashboard sort control + per-user card-fields picker** — the document list
gains a **sort control** (`document_date` | `added_date` × `asc` | `desc`,
round-tripped through the URL; relevance still wins while a query is present, so
the control is disabled then), and a per-user **Fields** popover that toggles
**and reorders** which metadata fields each card shows (order-significant,
persisted via `PUT /api/settings`). See [docs/api.md](docs/api.md) §1.3.1 and
[docs/frontend.md](docs/frontend.md).

**Admin reference-entity CRUD** — the admin API (`/api/admin/*`) now manages the
shared reference taxonomy, not just system/architecture/coverage/users:
**senders** (create / rename-or-merge / reassign-then-delete), **kinds**
(name-only rename with slug immutable, reassign-by-slug delete), **recipients**
(create / rename-or-merge / reassign-then-delete), and **series-aware currency
normalization** (a rename rewrites documents, authored series, and the
`series_insights` cache, and **refuses** up front if it would collide with a
user-authored override — no user data is dropped). All reference FKs are
`ON DELETE SET NULL`, and every mutation is guarded by a shared advisory lock.
Surfaced in `AdminView`'s **Metadata** tab. See [docs/admin.md](docs/admin.md)
and [docs/api.md](docs/api.md) §1.18.

**FX-rate seeding** — cross-currency series conversion needs one `fx_rates` row
per in-use currency (base = USD, date-aware). Admins can now seed a rate (live
fetch or manual) via `GET`/`POST /api/admin/fx-rates` and a Currencies-card
affordance, so a newly-normalised currency code no longer leaves FX conversion
silently missing. See [docs/api.md](docs/api.md) §1.18.7.

**Per-user per-kind tile border colours** — each user can colour dashboard tiles
by document kind (a per-user preference). The tile owns its border in the CSS
`components` layer so the accent paints reliably under Tailwind v4 cascade
layers, with an e2e guard asserting the computed border colour. See
[docs/frontend.md](docs/frontend.md).

**Stronger recipient / sender / date / kind extraction** — the metadata
extraction prompt and resolver were reworked so fields that were often blank or
wrong get filled reliably. The **recipient** now follows a priority ladder: the
name stated in the document itself (salutation "Dear/Beste/Geachte/T.a.v. …")
wins, creating a recipient from a **high-confidence** document-stated name even
when it was previously unknown; only then does it fall back to the email `To:`
user, then the forwarding/owner attribution. Forwarded mail's **original**
`To:`/`Aan:` is parsed from the quoted body so the real recipient beats the
dropbox address. The prompt now defines **due_date** vs **expiry_date** distinctly
(with the Dutch "vervaldatum" vs "verloopt" trap), ties the **salutation** to the
recipient and the **sign-off** to the **sender**, and adds a **kind** rubric. New
deterministic validation flags a mislabeled due/expiry date, a signed document
with no sender, and a personally-addressed document with no recipient. Consume-
folder and paperless-import documents can be attributed to a default owner via
`LIBRARY_IMPORT_DEFAULT_OWNER`, and `library backfill --kinds letter,invoice,receipt`
re-derives recipients on the existing corpus after the prompt-version bump. See
[docs/ingestion.md](docs/ingestion.md).

**Vision fallback for image-based PDFs** — on a scanned/image invoice whose OCR
captured only the letterhead, the amount, recipient, and date printed on the page
never reached the model, and the low-confidence retry re-read the same thin text.
Now a low-confidence extraction with a usable PDF/image original **re-attempts
with the file itself** (vision), so the model reads those fields off the page; it
stays within the existing two-call budget and falls back to text when the original
can't be sent. A deterministic `missing_amount` review flag catches the rarer
confident-but-wrong case (a payment/due term in the text but no amount extracted).
See [docs/ingestion.md](docs/ingestion.md) §"Input selection".

**Email-body ingestion when there's no attachment** — an inbound email with no
attachment is no longer dropped: its body is ingested as the document. See
[docs/ingestion.md](docs/ingestion.md).

**Ask metadata write tool (propose-then-confirm)** — the Ask agent can now
**update a document's metadata**, proposing the change and applying it only after
the user confirms. The write shares the edit path's validation recompute, so an
agent-applied fix clears its finding (and a bad edit re-flags) identically to the
`PATCH` route. See [docs/ask.md](docs/ask.md) and [docs/api.md](docs/api.md).

**Admin role + admin views** — users now have a boolean **admin** role
(`users.is_admin`, migration 0014). Admins reach a new **Admin** page (`/admin`)
with four tabs backed by `/api/admin/*`: **System** (app version + git sha,
redacted operational config, deployment topology, live DB stats), **Architecture**
(the architecture/ingestion docs rendered read-only), **Coverage** (backend +
frontend test coverage from a CI-baked summary), and **Users** (list, create, and
promote/demote/deactivate, with a last-active-admin lockout guard). Promote a user
from the host with `library user set-admin <name>` (or `library user add --admin`).
The role is exposed on `GET /api/auth/me`. See [docs/admin.md](docs/admin.md).

### Changed

**Document verification flow reworked** — edits now recompute validation.
`PATCH /api/documents/{id}` (and the Ask write tool, which shares the edit path)
re-run the deterministic rules on save, so correcting a flagged field **clears**
its finding while genuine warnings persist (previously a stale finding lingered
until the next pipeline run); a user-**verified** document stays verified across
clean edits. The detail page gains a prominent **"Why this needs review"** panel
listing every finding in plain language, dashboard rows show a short reason next
to the "Needs review" badge (via a new compact `review_findings` on the list
API), and a **step-through review queue** (`?queue=1`) walks the `needs_review`
set one document at a time, dropping each doc as it's fixed. See
[docs/frontend.md](docs/frontend.md).

**Project mutations are now admin-only** — projects are a global, shared taxonomy,
so `POST`/`PATCH`/`DELETE /api/projects` now require the admin role (`403`
otherwise); `GET /api/projects*` stays open to all authenticated users. See
[docs/api.md](docs/api.md) §1.15.

**Jobs view + live job notifications** — a new **Jobs** page (`/jobs`) lists
background/batch jobs split into Active and Recent, each enriched with its
document's pipeline stage, status, extraction cost, and any error. A navbar
indicator (spinner + count + dropdown) shows while documents are processing, and
toasts announce when a document finishes (success) or fails. Updates are pushed
live over Server-Sent Events (`GET /api/events`) backed by Postgres
`LISTEN/NOTIFY` — no polling. `GET /api/jobs` is now enriched with document
state and shows **one row per document** (a document's several jobs are
collapsed to its latest), so the same document isn't repeated. Document-less
system/periodic jobs (the scheduled email poll) are hidden by default — keeping
any that failed or are running — so their constant successes don't bury document
work; a "Show system tasks" toggle (and `GET /api/jobs?include_system=true`)
lists everything. See
[docs/jobs-and-notifications.md](docs/jobs-and-notifications.md),
[docs/api.md](docs/api.md) §1.8 / §1.8.4, and
[docs/architecture.md](docs/architecture.md) §1.4.1.

**`backfill-summaries` admin command** — `library backfill-summaries` enqueues
metadata extraction for indexed documents that have no summary (e.g. ingested
before summaries were generated), reusing the `extract_document` path so it
honours user-edited fields and the daily extraction budget. Throttleable with
`--limit N`. See [docs/ingestion.md](docs/ingestion.md) §"Backfill summaries"
and [docs/deployment.md](docs/deployment.md) §1.7.

**Document series + comparative queries** — Ask and the document detail view can
now compare a recurring bill to its usual values. A *series* is detected
automatically as the documents sharing one sender + kind (e.g. the monthly energy
bill from one provider). See [docs/ask.md](docs/ask.md) §1.7 and
[docs/api.md](docs/api.md) §1.13.

- New read-only module `library.series` computes series statistics on the fly
  (no new table or migration): distribution (count/mean/median/stdev/min/max per
  currency), a reference-vs-usual verdict (`higher`/`typical`/`lower`; "typical"
  = within ±1 stdev OR within `LIBRARY_SERIES_TYPICAL_PCT` of the median), a
  trend (`rising`/`falling`/`flat`), and a year-over-year comparison.
- New Ask tool `compare_to_series` joins `semantic_search` and `query_documents`
  in the tool-use loop, answering "more/less than usual", "vs last year", and
  "are my bills going up" questions with citations.
- New endpoint `GET /api/documents/{id}/series` returns the series summary plus
  per-point data; `status:"insufficient"` when the document has no sender/kind or
  too few siblings.
- New `DocumentSeriesTrend` widget on the detail view renders a Chart.js line
  chart of the series, highlighting the viewed document's point; it self-hides
  when there is no qualifying series.
- New settings `LIBRARY_SERIES_MIN_DOCUMENTS` (default `3`),
  `LIBRARY_SERIES_TYPICAL_PCT` (default `0.10`), and `LIBRARY_SERIES_FLAT_PCT`
  (default `0.05`).

**Conversational Ask** — Ask is now multi-turn. Follow-up questions like
*"what about last year?"* resolve against prior turns of the same conversation
instead of being answered cold. See [docs/ask.md](docs/ask.md) §1.6 and
[docs/api.md](docs/api.md) §1.11–1.12.

- Persistent conversation threads stored server-side in two new tables:
  `ask_threads` (one conversation, owner-scoped, titled from the first question)
  and `ask_turns` (one Q&A turn — question, answer, citations, cost, and the
  serialized Anthropic message blocks for replay).
- `ask_logs` is dropped and subsumed by `ask_turns` (migration 0008). `ask_logs`
  had no readers; existing rows are discarded.
- History replay: the engine loads the last `LIBRARY_ASK_HISTORY_TURNS` (default
  3) turns and prepends their message blocks (Q&A + tool results) into the
  Claude call, so follow-ups can reason over earlier evidence without re-querying.
- Prompt caching: the rehydrated history prefix and the static system+tools
  definitions each carry an Anthropic `cache_control: ephemeral` breakpoint,
  reducing cost and latency on follow-ups.
- New `LIBRARY_ASK_HISTORY_TURNS` setting (default `3`); `0` disables history.
- New thread CRUD endpoints: `GET /api/ask/threads`,
  `GET /api/ask/threads/{id}`, `DELETE /api/ask/threads/{id}`.
- `POST /api/ask` gains optional `thread_id` on request and returns `thread_id`
  on response.
- Chat UI: `AskView` is now a scrollable transcript, with a follow-up input and
  a conversation sidebar (resume, delete, new conversation). Routes `/ask` and
  `/ask/:threadId`.

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

- **Document preview now renders PDFs in-app, identically across browsers.** The
  detail page (`/documents/:id`) previously embedded the PDF in a native
  `<iframe>`, which broke differently in each engine — Chrome forced a
  "Pages/Manage" panel over the document, Firefox showed its own toolbar and
  thumbnail sidebar, and Safari rendered a black box. The new
  `DocumentPdfPreview` component renders every page to `<canvas>` with pdf.js
  (`pdfjs-dist`), fit-to-width and lazily via `IntersectionObserver`, so all
  pages are **scrollable** and the result is the same in Chrome, Firefox, and
  Safari. Shows a thumbnail poster while loading and Open/Download fallbacks for
  render failures and password-protected PDFs. The Playwright matrix gained
  desktop Firefox and WebKit projects plus a cross-browser preview spec. See
  [docs/frontend.md](docs/frontend.md). Adds the `pdfjs-dist` dependency (worker
  lazy-loaded, off the initial bundle).

- `db` image is now `pgvector/pgvector:pg17` (was `postgres:17.5-alpine`),
  initialised with `C.UTF-8` so text ordering stays byte-wise and an existing
  C-collation `pgdata` volume is reused safely. **The LXC now wants ~6–8 GB
  RAM** for the embedder — see [docs/deployment.md](docs/deployment.md) §1.7.1
  for the upgrade path.

### Fixed

- **Upload: the "Select at least one file" error now clears when you pick a
  file.** Previously the validation error from a premature submit lingered on
  screen even after a valid selection (it was only cleared by the next submit),
  making the picker look broken. `UploadView` now watches the selection and
  clears the error as soon as a file is chosen.

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
