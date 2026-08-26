# Architecture

**Status:** active. **Last updated:** 2026-08-26 (§1.6 module map: added `src/library/structured_query.py` — it crossed the 400-line module-map floor on the `feat/ask-answer-trustworthiness` branch, which added the `Coverage`/`Aggregated` machinery). Earlier (2026-08-20, §1.6 module map: added `src/library/llm/`, the subscription LLM backend — Agent SDK adapter + OAuth refresh; and §1.3 data model: `instance_settings`, migration 0030, the runtime override layer behind the LLM backend switch; see `llm-backends.md`). Earlier (2026-08-12, documentation verification sweep): §1.5.1 now names the two genuinely per-user resources — Ask threads and saved views — instead of claiming no ownership anywhere; Inter is CDN-loaded, not self-hosted; corrected the module-map gap wording). Earlier (2026-07-17, business matters: business matters: the `matters`/`document_matters` collection (migration 0028) and the separate best-effort matter-classification pass after extract; §1.2 step 3, §1.3). Earlier (2026-07-15, data model: `email_selection_traces`, the per-email skip audit, migration 0027). Earlier (2026-07-06): authorization model §1.5.1: shared library, no per-user ownership — deliberate. Earlier (2026-06-30): quote kind, chart title/description overrides, authored series, recipient↔user link.
**Last verified:** 2026-08-26 — method: added the `src/library/structured_query.py` row to §1.6's module map (`wc -l` confirms 407 lines, past the 400-line `MODULE_MAP_LINE_FLOOR`) and ran `scripts/check_docs.py`, which now passes with zero violations. This was a single-row addition only — nothing else in the document was re-checked this pass; the rest carries forward the 2026-08-20 verification below unchanged. That verification's method was: re-derived §1.6's module map from `ls src/library/` and confirmed the new `src/library/llm/` row against the package on disk (`oauth.py`, `subscription.py`, `backends.py`), and the §1.3 `instance_settings` entry against `models.py` and `migrations/versions/0030_instance_settings.py` and its link target `docs/llm-backends.md`. The rest of the document is unchanged since the 2026-08-12 sweep, whose method was: checked §1.6's module map against `wc -l` of every module and the floor rule in `scripts/check_docs.py`, the pipeline order against `_NEXT_STATUS` and each stage hook in `jobs.py`, the §1.3 table against `__tablename__` in `models.py` plus `migrations/versions/`, the §1.5 claims against every `/api` router's owner-scoping, and each model id against `config.py`.

Library is a self-hosted personal/family document archive. This document
describes the system design. The original decision record (with research and
rejected alternatives) is archived at
[archive/260610-project-inception-decisions.md](archive/260610-project-inception-decisions.md)
and [archive/260610-greenfield-build-plan.md](archive/260610-greenfield-build-plan.md)
— both are historical and describe the system as *planned*, not as built.
Per-feature design specs and plans live in [superpowers/](superpowers/), and the
development journal in [../journal/](../journal/).

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
   `messages.parse()`) turns the document — normally its OCR text; for a
   thin scan (average OCR text below
   `LIBRARY_EXTRACTION_VISION_MIN_CHARS_PER_PAGE` chars/page) or
   unusable OCR, the original file itself (vision) — into metadata: kind, sender, title,
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
   see [ingestion.md](ingestion.md) "Extraction quality". Once extraction
   lands, a **separate best-effort matter-classification pass** is deferred as
   its own job (never blocking the pipeline): a second, cheap Anthropic call
   auto-files the document into 0..n existing **business matters** (evergreen
   subject categories) — kept out of the main extraction call so the vocabulary
   can change and the corpus be re-filed cheaply without re-running full
   extraction. Merge-only, budget-gated, and skipped for user-curated matters;
   see [ingestion.md](ingestion.md) "Matter classification".
4. **Markdown** — Claude vision (Haiku 4.5 by default) rasterizes each
   page and renders a clean GitHub-flavored markdown representation,
   grounded on the OCR text. One `messages.parse()` call per page-image
   batch; results are stored per page in `document_pages`. **Born-digital
   `text/markdown`/`text/plain` bypass the vision model entirely**: the raw
   content is already the text layer, so it is written as a single
   `document_pages` row with no API call or budget spend. Best-effort,
   exactly like extraction: disabled feature, missing API key, blown
   budget, unusable input, or an API error all produce a skip/failed event
   and the document continues to `embed`. At the tail of this stage a
   **fill-only extraction repair pass** (`library.extraction.repair`) gets one
   look at the fresh page markdown: when validation still reports
   `missing_date`/`missing_sender`/`generic_sender`, a single cheap call fills
   only the null fields (or replaces a generic-named sender) and revalidates —
   equally best-effort, audited via `extraction_repair_completed`/`_skipped`
   events. See [ingestion.md](ingestion.md) "Markdown layer" and
   "Fill-only extraction repair".
5. **Embed** — the document's text is chunked and embedded (bge-m3, 1024-dim)
   by the local `embedder` sidecar; vectors land in `document_chunks` (HNSW
   index) for semantic retrieval. When `document_pages` exist the embed
   stage chunks each page's markdown and tags every chunk with its
   `page_number`; otherwise it falls back to `ocr_text` with
   `page_number = NULL`. Best-effort: a document that fails to embed still
   reaches `indexed` and stays searchable by full-text. See [ask.md](ask.md).
6. **Index** — metadata and text become searchable (Postgres FTS, both
   Dutch and English stemming; semantic retrieval via `document_chunks`) and
   visible in the UI. The two STORED generated tsvector columns fold in
   `title`, `summary`, `coalesce(pages_markdown, ocr_text)` — preferring the
   vision "understood layer" and falling back to raw OCR (migration
   `0025_fts_page_markdown`) — **and `topics`** (the auto-extracted subject
   phrases, via an immutable `topics::text` cast; migration `0012_topics_fts`),
   so a document is findable by its topics and by body text OCR never captured.

## 1.3 Data model (summary)

The central table is `documents`; everything else hangs off it. Originals on
disk are immutable — every other table (embeddings, page markdown, etc.) is a
re-derivable artifact.

**`documents`** — one row per ingested file. Key columns:

- Identity & lifecycle: `hash`, `mime`, lifecycle `status`, `review_status`
  enum, uploader, source channel.
- Extracted metadata: `title`, `summary`, `document_date`, `language`,
  `amounts`/`expiry`, OCR text + confidence.
- `topics` — JSONB list of human-readable subject phrases (general-reference
  material).
- `extra` — JSONB for kind-specific fields, plus `extra["validation"]` and
  `extra["corrections"]`.

**Lookup tables** (FKs from `documents`):

- `senders` — who a document is from.
- `recipients` — who it is addressed to (mirrors `senders`, nullable FK;
  migration 0016 seeds a "John" recipient and backfills existing docs). An
  optional `recipients.user_id` (migration 0020) links a recipient to a user:
  creating a user auto-links a recipient named by their display name. Ingestion
  fills the recipient via a priority ladder — the recipient **named in the
  document** (salutation) first, then the email `To:` user, then the uploader/
  owner — creating a plain recipient from a high-confidence document-stated name
  when it matches no known person (see ingestion.md, "Applying results", and
  admin.md §1.2.4).
- `kinds` — document type. Seeded: invoice, receipt, certificate, utility
  bill, parking ticket, warranty, manual, reference, research, note, letter,
  contract, ticket, other (general-reference kinds added in migration 0010
  alongside `topics`), and `quote` (migration 0017). Users can add kinds
  inline via `POST /api/kinds` (slugified, case-insensitively deduped, with a
  near-duplicate guard). Quotes are estimates, **not** real expenditure, so
  they are excluded from spend totals (see [ask.md](ask.md)).

**Collections:**

- `tags` — many-to-many.
- `projects` — first-class collections (`projects` + `document_projects`,
  migration 0011). Soft-archived via `archived_at`; documents survive a
  project delete.
- `matters` — evergreen business-subject categories (`matters` +
  `document_matters`, migration 0028), each with a `hint` the LLM matter
  classifier reads. Also soft-archivable via `archived_at`; documents survive a
  matter delete. Populated automatically (see §1.2 step 3) but hand-editable.

**Derived artifacts** (rebuildable from the original):

- `document_pages` — per-page markdown renderings, PK `(document_id, page_number)`.
- `document_chunks` — per-chunk embeddings (pgvector + HNSW). Each chunk carries
  `page_number` when generated from `document_pages`, `NULL` when falling back
  to `ocr_text`.
- `ingestion_events` — append-only audit trail.
- `held_emails` — lifecycle records for emails the email-in triage held for
  review instead of filing (`held` → `ingested`/`dismissed`, migration 0026);
  snapshots the per-item decision trace and points at the message in the Held
  IMAP folder by Message-ID. See [ingestion.md](ingestion.md), "Held for
  review".
- `email_selection_traces` — append-only per-email skip audit (migration 0027):
  one row (with the full decision list) per processed email whose selection
  filtered or dropped at least one item, so an email whose items were all
  skipped still leaves a durable record. Read via
  `GET /api/settings/email-triage/recent-skips`. See the
  [email-triage runbook](runbooks/email-triage.md) §6.
- `note_versions` — append-only; one (title, body) snapshot per edit/restore of
  an in-app note (`source = note`, migration 0013, which also adds `note` to the
  `document_source` CHECK).

**Ask & series:**

- `ask_threads` — one conversation per owner.
- `ask_turns` — one Q&A turn per thread; stores cost/provenance and the
  serialized Anthropic message blocks replayed into follow-up questions.
- `series_insights` — cached per-series prose, one row per
  `(sender_id, kind_id, currency)` (see [ask.md §1.7](ask.md)).
- `series_membership_overrides` — durable manual `pin`/`exclude` keyed by
  `(sender_id, kind_id, currency, document_id)`, applied on every series
  computation (migration 0015; see [api.md §1.15](api.md)).
- `series_meta_overrides` — user-set title/description override per emergent
  series, keyed by `(sender_id, kind_id, currency)` (migration 0018). Powers
  the editable chart title/description and the single-chart route
  `/charts/:seriesId`.
- `authored_series` + `authored_series_members` — user-curated ("manual")
  series: a named, optionally-currency-scoped collection of documents that
  produces its own chart even without a natural emergent seed (migration 0019).
  Addressed as `a-{id}`; summarised through the same code path as emergent
  series.
- `fx_rates` — small reference FX snapshot (USD base, date-aware) for
  converting cross-currency pins.

**Auth:** `users`, `sessions`, `api_tokens`.

**Instance configuration:**

- `instance_settings` — key/value (JSONB) store for instance-wide operational
  settings an admin can change at runtime, without a redeploy or restart
  (migration 0030). Currently holds only `llm_backend.<surface>`, selecting the
  metered API or a Claude subscription per LLM surface — see
  [llm-backends.md](llm-backends.md). Deliberately an **override layer**: a
  missing row means "use the environment value", so an empty table behaves
  exactly as the deployed configuration says, and reverting a setting is
  deleting its row. This is the third kind of configuration in the system,
  alongside per-user display preferences (`users.preferences`) and startup
  environment variables (`Settings`); it exists because a value that is both
  instance-wide *and* mutable at runtime fits neither.

## 1.4 Interfaces

- **REST API** (`/api`) — versioned, cookie- or bearer-authenticated,
  OpenAPI-documented. The full product surface: search, CRUD, downloads,
  ingestion, in-app **note authoring** (`/api/notes` — create / edit-in-place /
  version history / restore, see [api.md §1.17](api.md)), job status, and
  natural-language **Ask** (`POST /api/ask`, see [ask.md](ask.md)).
- **MCP server** (`/mcp`) — FastMCP over streamable HTTP, bearer tokens.
  Tools for searching, reading, and ingesting documents from LLM clients.
- **Web app** — Vue 3 SPA using the **Mosaic** (Cruip) theme: violet accent,
  soft rounded-xl cards, dark mode, Inter loaded from Google Fonts. Content-first,
  responsive 320px-up, accessible. (Reskinned from GOV.UK — see
  [frontend.md](frontend.md) and `journal/260613-mosaic-reskin.md`.)

### 1.4.1 Live job events

Document processing runs in the **worker**, but the UI lives in the **api**
process. They are bridged by Postgres `LISTEN/NOTIFY`: as a document moves
through the pipeline, `library.jobs.notify_document_event` emits a `NOTIFY` on
the `library_doc_events` channel (best-effort — a notify failure never strands a
document). The api process runs a single process-wide events broker
(`library.events_broker`) that holds *one* `LISTEN` connection and fans each
notification out in-process to every connected client; the Server-Sent Events
endpoint (`GET /api/events`, `library.api.events`) just drains a per-client
queue. SSE Postgres usage is therefore capped at one connection per process
regardless of how many browser tabs are streaming.

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

A single boolean **admin** role (`users.is_admin`) layers on top: admins
gate global project mutations and an admin-only views surface
(`/api/admin/*`, the `/admin` page). See [admin.md](admin.md).

### 1.5.1 Authorization model — shared library, no per-user ownership

This is a **deliberate design decision**, stated here so it is not mistaken for
a gap: **the corpus itself has no per-user resource ownership.** Authentication
is the only gate on it, and beyond the admin/non-admin split every authenticated
user has full read **and write** access to the *entire* library.

- The API router applies `current_user` + CSRF globally (`src/library/app.py`),
  so anonymous requests are rejected (`401`). But no *corpus* endpoint checks the
  caller against a resource's creator. Any signed-in user can view, edit, and
  delete **any** document, its metadata, notes, **comments**, tags, projects,
  matters and series — not only the ones they created.
- `documents.uploader_id` and `document_comments.author_id` are **provenance /
  attribution** ("who added this"), surfaced for context. They are **not**
  authorization boundaries and are never enforced on read or mutation.
- **Two exceptions, both per-user by design and genuinely enforced:**
  **Ask conversations** (`ask_threads`) and **saved views** (`saved_views`).
  Each is scoped to `user_id` and another user's row 404s rather than loading —
  `src/library/api/ask.py` (`thread.user_id != user.id`) and
  `src/library/api/saved_views.py` (`_get_owned_view_or_404`). These are
  personal working state, not shared corpus, which is why they are scoped.
- The only elevated boundary is **admin** (`users.is_admin`), which gates global
  taxonomy/project mutations, user management, and the `/admin` surface.

**Why:** the deployment is a single household — named family accounts sharing
one library (§1.5). The whole point is a shared, fully visible and collaboratively
editable corpus (e.g. two family members annotating the same document), not
per-user silos. Per-user ACLs would work against that.

**For reviewers and automated tooling:** a finding that assumes per-user
ownership — e.g. "IDOR: user A can edit user B's comment" — describes the
*intended* model in this deployment, not a vulnerability. This decision would
need revisiting **only** if the app is ever opened beyond a single trusted
household; at that point per-resource authorization would have to be added
across documents, notes, comments, and taxonomy alike.

## 1.6 Module map

Where things live under `src/library/`. Packages first, then the substantial
top-level modules.

| Package | What is in it |
| --- | --- |
| `src/library/api/` | HTTP API routers, mounted under `/api` by the application factory. The admin surface is the nested `api/admin/` package. |
| `src/library/ask/` | Natural-language question answering over the archive: retrieval, the agentic tool loop, citations. |
| `src/library/auth/` | Argon2 passwords, cookie sessions, bearer API tokens. |
| `src/library/embedding/` | Local embedding (bge-m3 via a text-embeddings-inference sidecar). |
| `src/library/extraction/` | Claude metadata extraction: schema, extractor, pricing, and pipeline glue. |
| `src/library/importer/` | paperless-ngx importer: REST client, payload mapper, batch runner. |
| `src/library/llm/` | The subscription LLM backend: Claude Agent SDK adapter and OAuth credential refresh. See [`llm-backends.md`](llm-backends.md). |
| `src/library/markdown/` | Vision-model page-to-markdown conversion and its storage. |
| `src/library/ocr/` | Routed OCR: text-layer extraction, OCRmyPDF/Tesseract, OpenCV+RapidOCR. |

| Module | What it does |
| --- | --- |
| `src/library/email_ingest.py` | Email-in ingestion: poll an IMAP mailbox and ingest its attachments. |
| `src/library/models.py` | SQLAlchemy 2.0 declarative models — the data model in §1.3. |
| `src/library/series.py` | Recurring-document series detection and comparative statistics. |
| `src/library/schemas.py` | Pydantic request/response schemas for the HTTP API. |
| `src/library/cli.py` | The `library` administration CLI (typer): accounts, imports, sweeps. |
| `src/library/jobs.py` | Procrastinate job queue wiring and the document-processing pipeline (§1.2). |
| `src/library/taxonomy.py` | Taxonomy listing: kinds, senders, recipients, tags, with counts. |
| `src/library/mcp_server.py` | The MCP server: FastMCP tools over the archive (§1.4). |
| `src/library/notifications.py` | Pushover push notifications (per-user, opt-in). |
| `src/library/search.py` | Shared document query building for the REST API and the MCP server. |
| `src/library/series_insight.py` | Precomputed, cached LLM prose description for a series. |
| `src/library/consume.py` | Consume-folder watcher: ingest files dropped into a watched directory. |
| `src/library/ingest.py` | Ingestion service: bytes in, `Document` row + queued job out. |
| `src/library/config.py` | Settings, loaded from the environment with the `LIBRARY_` prefix. |
| `src/library/structured_query.py` | Structured aggregation over extracted metadata (`sum_amount`, `distinct_senders`, `list_documents`) behind the `/ask` `query_documents` tool; each result carries a `Coverage` object reporting what the rows leave out. |

Everything else under `src/library/` is a single-purpose helper of under ~300
lines — storage, FX, matters, projects, PDF unlock, the events broker and
similar — named plainly enough to find by filename.

**This table is gated.** `scripts/check_docs.py` fails CI when a listed path
does not exist, and when a package or a top-level module of at least
`MODULE_MAP_LINE_FLOOR` lines is missing from it. The floor is **400**, not the
~300 boundary this table actually documents: module sizes cluster tightly below
372 lines (`email_label.py` sits at 299), so a floor down there would push
modules in and out of the mandatory set on single-line edits and red CI for no
real change. 400 sits in the middle of the one wide gap in the distribution
(512 → 372) — the one near the floor; the distribution has a wider one higher
up, at 1015 → 643 — so crossing it means a module genuinely grew. Listing **more** than
the floor requires is deliberate and always allowed — an entry is never a
violation for being small.
