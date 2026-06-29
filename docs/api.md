# REST API

**Status:** active. **Last updated:** 2026-06-29 (admin recipient management: `PATCH`/`DELETE /api/admin/recipients/{id}` — rename/merge + reassign-then-delete, §1.18.1–§1.18.2; recipient field: `GET /api/recipients`, `recipient` in document responses + PATCH body, `recipient_id` list filter).

The REST API is a first-class product surface: everything the web app can
do is available to scripts, shortcuts, and other tools over plain HTTP.
Interactive OpenAPI documentation is served at `/docs` (schema at
`/openapi.json`).

All endpoints live under the `/api` prefix and exchange JSON unless noted.
Decimal money values (`amount_total`) are serialized as JSON **strings**
(e.g. `"123.45"`) to preserve precision.

**Every `/api` endpoint requires authentication** (session cookie or
bearer token — see 1.9) except `POST /api/auth/login`. `/healthz` is open
(container healthcheck, no database access). Unauthenticated requests get
`401` with the generic body `{"detail": "not authenticated"}`.

## 1.1 Endpoint summary

| Method | Path | Purpose |
|--------|------|---------|
| POST   | `/api/auth/login` | Log in; sets session + CSRF cookies (no auth required) |
| POST   | `/api/auth/logout` | Log out; revokes the session, clears cookies |
| GET    | `/api/auth/me` | The authenticated user |
| GET    | `/api/auth/tokens` | List your API tokens (never their secrets) |
| POST   | `/api/auth/tokens` | Create an API token; secret shown **once** |
| DELETE | `/api/auth/tokens/{id}` | Revoke one of your API tokens |
| POST   | `/api/ask` | Ask a natural-language question; cited answer |
| GET    | `/api/ask/threads` | List your Ask conversations |
| GET    | `/api/ask/threads/{id}` | Full thread detail (all turns) |
| DELETE | `/api/ask/threads/{id}` | Delete a conversation and its turns |
| POST   | `/api/documents` | Upload a file for ingestion |
| GET    | `/api/documents` | List / search documents |
| GET    | `/api/documents/{id}` | Full document detail |
| PATCH  | `/api/documents/{id}` | Edit metadata |
| DELETE | `/api/documents/{id}` | Soft-delete |
| POST   | `/api/documents/{id}/extract` | Queue metadata re-extraction |
| POST   | `/api/documents/{id}/verify` | Mark document metadata as verified |
| GET    | `/api/documents/{id}/original` | Download the original file |
| GET    | `/api/documents/{id}/searchable.pdf` | Download the OCR searchable PDF |
| GET    | `/api/documents/{id}/thumbnail` | First-page WebP thumbnail |
| GET    | `/api/documents/{id}/series` | Recurring-series stats + comparison for this document |
| POST   | `/api/notes` | Author a new in-app markdown note |
| PATCH  | `/api/notes/{id}` | Edit a note's title/body in place (snapshots a version) |
| GET    | `/api/notes/{id}/versions` | A note's version history (newest first) |
| POST   | `/api/notes/{id}/versions/{version_no}/restore` | Restore a note to a previous version |
| GET    | `/api/charts` | Every eligible recurring `(sender, kind)` series, summarised |
| POST   | `/api/series/{sender_id}/{kind_id}/members` | Pin a document into a series (or clear an exclude) |
| DELETE | `/api/series/{sender_id}/{kind_id}/members/{document_id}` | Exclude a document from a series (or clear a pin) |
| GET    | `/api/kinds` | Document kinds with counts |
| POST   | `/api/kinds` | Create a document kind (dedupes / rejects near-duplicates) |
| GET    | `/api/senders` | Senders with counts |
| GET    | `/api/recipients` | Recipients with counts |
| GET    | `/api/tags` | Tags with counts |
| GET    | `/api/projects` | List projects/collections with counts |
| POST   | `/api/projects` | Create a project (admin only) |
| GET    | `/api/projects/{slug}` | Project detail |
| PATCH  | `/api/projects/{slug}` | Edit a project (name/description/archived) (admin only) |
| DELETE | `/api/projects/{slug}` | Delete a project (memberships cascade) (admin only) |
| GET    | `/api/jobs` | Recent background jobs (enriched with document state); filter by `task_name`/`document_id` |
| GET    | `/api/jobs/task-names` | Distinct task names (for the task-type filter) |
| GET    | `/api/events` | Live document-pipeline events (Server-Sent Events) |
| GET    | `/api/settings` | Your display preferences (dashboard fields + page-canvas tone + tile preview) |
| PUT    | `/api/settings` | Update your dashboard fields |
| PUT    | `/api/settings/appearance` | Update your page-canvas tone and tile preview |
| PUT    | `/api/settings/notifications` | Update your Pushover notifications + email forwarding addresses |
| GET    | `/api/admin/system` | System & infra context: version, config, deployment, DB stats (admin only) |
| GET    | `/api/admin/architecture` | Architecture docs as markdown (admin only) |
| GET    | `/api/admin/coverage` | Latest CI-generated test coverage (admin only) |
| GET    | `/api/admin/users` | List all users (admin only) |
| POST   | `/api/admin/users` | Create a user (admin only) |
| PATCH  | `/api/admin/users/{id}` | Promote/demote, activate/deactivate a user (admin only) |
| PATCH  | `/api/admin/recipients/{id}` | Rename or merge a recipient (admin only) |
| DELETE | `/api/admin/recipients/{id}` | Delete a recipient, reassigning its documents (admin only) |

Soft-deleted documents return **404** from every per-document endpoint and
never appear in lists. Other error shapes: `404` unknown document, `422`
validation problem (FastAPI detail body), `409`/`413`/`415` on upload (see
[ingestion.md](ingestion.md)).

## 1.2 Upload — `POST /api/documents`

Multipart upload (`file` field). `201` with `{id, sha256, status,
duplicate}` for a new document, `200` for duplicate content (pointing at
the existing document), `409` if the content matches a soft-deleted
document, `413` over the size limit, `415` unsupported type. The
authenticated user is recorded as the document's uploader. Full
ingestion semantics are documented in [ingestion.md](ingestion.md).

## 1.3 List and search — `GET /api/documents`

### 1.3.1 Query parameters

| Param | Type | Meaning |
|-------|------|---------|
| `q` | string | Full-text search, [websearch syntax](https://www.postgresql.org/docs/current/textsearch-controls.html) (quoted phrases, `OR`, `-exclusion`) |
| `kind` | string | Kind slug (e.g. `invoice`) |
| `sender_id` | int | Sender id |
| `recipient_id` | int | Recipient id |
| `tag` | string, repeatable | Tag slug; repeating the parameter ANDs them (`?tag=energie&tag=wonen` requires both) |
| `project` | string | Project slug; only documents that belong to this project |
| `language` | enum | `nld` / `eng` / `mixed` / `unknown` |
| `status` | enum | `received` / `ocr` / `extract` / `embed` / `indexed` / `failed` |
| `date_from`, `date_to` | date | Inclusive bounds on `document_date` |
| `review_status` | enum | `verified` / `needs_review` / `unreviewed` — filter by extraction-quality review state |
| `source` | enum | `upload` / `consume` / `email` / `api` / `mcp` / `import` / `note` |
| `limit` | int | Page size, default 25, max 100 |
| `offset` | int | Rows to skip, default 0 |

All filters compose (AND), including with `q`.

### 1.3.2 Response

```json
{
  "items": [
    {
      "id": 12, "title": "Energierekening mei 2026", "summary": "…",
      "kind": {"slug": "invoice", "name": "Invoice"},
      "sender": {"id": 3, "name": "Eneco"},
      "recipient": {"id": 1, "name": "John"},
      "tags": [{"slug": "energie", "name": "Energie"}],
      "projects": [{"slug": "house-purchase", "name": "House purchase"}],
      "document_date": "2026-05-15", "language": "nld",
      "status": "indexed", "review_status": "unreviewed",
      "mime_type": "application/pdf",
      "page_count": 2, "created_at": "2026-06-10T12:00:00Z",
      "has_searchable_pdf": true, "has_thumbnail": true,
      "amount_total": "123.45", "currency": "EUR",
      "snippet": "uw <b>rekening</b> voor mei … totaal",
      "rank": 0.31
    }
  ],
  "total": 1, "limit": 25, "offset": 0
}
```

`total` is the filtered count before pagination. `snippet` and `rank` are
only present (non-null) when `q` is given. Tags and `projects` are each
sorted by slug; `projects` is `[]` when the document is in no project.
`review_status` reflects extraction-quality validation: `unreviewed` (no
issues found), `needs_review` (one or more validation findings), or
`verified` (user confirmed the metadata is correct). `amount_total` (JSON
string, preserves decimal precision) and `currency` (3-letter code) are
`null` when not set on the document.

### 1.3.3 Search semantics

- `q` is parsed with `websearch_to_tsquery` and matched against **both**
  generated tsvector columns — `search_vector_nl` (`dutch` config) and
  `search_vector_en` (`english` config), OR-combined. Stemming therefore
  works in both languages: `q=rekening` finds "rekeningen", `q=policy`
  finds "policies".
- Each vector folds in `title`, `summary`, `ocr_text`, **and `topics`** (the
  auto-extracted subject phrases, cast with `coalesce(topics::text,'')`;
  migration `0012_topics_fts`), so a document is findable by its topics even
  when the term never appears in its body. `topics` is read-only (see §1.5).
- The rank is `greatest(ts_rank(nl), ts_rank(en))` — the best of the two
  language interpretations — and results are ordered by it, descending.
- `snippet` is `ts_headline` over `ocr_text`, generated with whichever
  config produced the higher rank, capped with
  `MaxFragments=2, MaxWords=12, MinWords=4, ShortWord=2,
  FragmentDelimiter=" … "` and the **default `<b>`/`</b>` markers**.

> **Rendering snippets safely.** `ocr_text` is raw OCR output and is NOT
> HTML-escaped by the server; a scanned document could contain literal
> HTML. Clients must render the snippet as plain text and handle the
> `<b>`…`</b>` markers deliberately (e.g. escape everything, then convert
> the known markers back to highlighting). Never inject a snippet into the
> DOM as HTML.

Without `q`, ordering is `document_date DESC NULLS LAST, created_at DESC`.

## 1.4 Detail — `GET /api/documents/{id}`

Everything in the list item, plus:

- `ocr_text`, `ocr_confidence`
- `amount_total` (JSON string), `currency`, `due_date`, `expiry_date`
- `source`, `original_filename`, `sha256`
- `extraction` — the provenance block written by Claude extraction
  (`prompt_version`, `model`, `confidence`, token/cost accounting,
  `fields_set`, …), or `null` if extraction has not run. This is a
  deliberate subset: the raw `extra` JSONB column is not exposed
  wholesale.
- `validation` — the latest deterministic-validation run:
  `{prompt_version, findings: [{rule, field, severity, message}, …],
  validated_at}`. `findings` is an empty list when no rules fired. `null`
  if validation has not run yet. See [ingestion.md](ingestion.md)
  "Extraction quality" for the rule table.
- `user_edited_fields` — fields locked by user edits (see 1.5)
- `events` — the full ingestion audit trail, oldest first:
  `[{event, detail, created_at}, …]`

## 1.5 Edit metadata — `PATCH /api/documents/{id}`

JSON body; only the fields present in the body change. Editable fields:

| Body field | Notes |
|------------|-------|
| `title`, `summary` | `null` clears |
| `document_date`, `due_date`, `expiry_date` | ISO dates, `null` clears |
| `kind_slug` | Must be an existing kind slug (`422` otherwise); `null` clears the kind |
| `sender` | Sender **name**; upserted case-insensitively (same rule extraction uses); `null` clears |
| `recipient` | Recipient **name**; upserted case-insensitively (same rule extraction uses); `null` clears |
| `tags` | **Full replacement** list of slugs; unknown slugs are created; `[]` clears; `null` is rejected |
| `projects` | **Full replacement** list of project slugs *or names*; unknown identifiers are upserted (a name becomes a new project, slugified); `[]` clears membership; `null` is rejected. Also appends a `project_changed` ingestion event |
| `language` | `nld` / `eng` / `mixed` / `unknown`; `null` rejected |
| `amount_total` | Decimal as string or number; `null` clears |
| `currency` | 3-letter code, normalized to upper case; `null` clears |

Every edited field is appended to `extra["user_edited_fields"]` (mapped to
storage names: `kind_slug`→`kind_id`, `sender`→`sender_id`,
`recipient`→`recipient_id`). Re-extraction
(W6) honours this list and never overwrites user-edited fields. An
ingestion event `user_edited` is recorded with the changed field names.
Returns the updated document detail.

> **`topics` is read-only.** The auto-extracted `topics` list is **not** in this
> body (it was removed from `DocumentUpdate` and the detail editor). It still
> appears on every list/detail response (and the MCP document summary) and is
> now indexed for full-text search (§1.3.3), but it is owned by extraction, not
> the user. `tags` remains the curated, editable cross-document filter facet.

## 1.6 Delete — `DELETE /api/documents/{id}`

Soft delete: sets `deleted_at`, records a `deleted` ingestion event,
returns `204`. The document then 404s on every endpoint and disappears
from lists; its file and row are kept. Re-uploading identical content
returns `409` (see ingestion.md). **Restore is out of scope for now** —
undeleting means clearing `deleted_at` manually in the database.

## 1.7 File downloads

- `GET /api/documents/{id}/original` — streams the stored original with
  its real `Content-Type` and a `Content-Disposition: attachment` header
  carrying the original filename.
- `GET /api/documents/{id}/searchable.pdf` — the OCR-produced searchable
  PDF (`application/pdf`); `404` if the document has none (text-layer
  PDFs, photos, and plain text don't produce one).

Both endpoints take `?disposition=inline|attachment` (default
`attachment`; anything else is a `422`). `inline` keeps the filename in
the header but lets the browser render the file instead of downloading
it — the detail page's previews depend on this: the image preview
renders inline in an `<img>`, and the PDF preview (rendered to canvas by
pdf.js in `DocumentPdfPreview.vue`) plus its "Open in new tab" link fetch
the inline URL, because an attachment response shows nothing inline and
triggers a download instead.
- `GET /api/documents/{id}/thumbnail` — first-page thumbnail,
  `image/webp`, ~480 px wide. Generated by a background job after OCR;
  `404` until it exists (and always for plain-text documents).
  `has_thumbnail` in list/detail responses reflects file existence.

## 1.8 Jobs — `GET /api/jobs`

Most recent document-processing work (newest first) from the Procrastinate
queue, enriched with each document's pipeline state. `limit` 1–500, default 50.

**One row per document.** A document spawns several jobs (`process_document`,
`generate_thumbnail`, and the per-document backfill tasks); the endpoint
collapses them to a single row — the document's **most recent** job — so the
same document isn't repeated. `id` / `task_name` / `status` / `started_at` /
`finished_at` are that latest job's; `document_*` / `cost_usd` / `error` are
document-level. `started_at` / `finished_at` come from Procrastinate's
`procrastinate_events` table (the job's last `started` and last terminal event),
so the UI can show a timestamp and compute a run duration.

By default, document-less system/periodic jobs (the scheduled email poll) are
omitted when they succeeded — they fire constantly and would bury document work
— while any that **failed or are still running** are kept, so a broken poller
stays visible. Pass `include_system=true` to list them too (still one row per
document; system jobs are not deduplicated, having no document to group by).

**Filters.**

- `task_name=<fully-qualified name>` — restrict to a single task type (e.g.
  `library.jobs.poll_email_inbox`). A task filter implies system rows are shown,
  so it overrides the hide-succeeded-system-tasks default.
- `document_id=<id>` — **history mode**: returns *every* job for that one
  document (uncollapsed), newest first, so a document's full processing history
  can be traced. The per-document collapse and the hide-system default do not
  apply in this mode.

Each row:

```jsonc
{
  "id": 123,
  "status": "doing",            // Procrastinate status: todo|doing|succeeded|failed|…
  "task_name": "library.jobs.process_document",
  "attempts": 0,
  "scheduled_at": "2026-06-23T10:00:00Z",
  "started_at": "2026-06-23T10:00:01Z",  // last `started` event, else null
  "finished_at": "2026-06-23T10:00:04Z", // last succeeded/failed/aborted event, else null
  "document_id": 42,            // null for document-less jobs (e.g. email poll)
  "active": true,               // status is todo or doing
  "document_title": "Energierekening",  // null if no/deleted document
  "document_status": "ocr",     // current pipeline stage, or terminal indexed/failed
  "error": "ocr exploded",      // latest `failed` event detail, else null
  "cost_usd": 0.0123,           // extraction cost from document provenance, else null
  "tokens": 1500                // extraction input+output tokens, else null
}
```

The `document_*` / `error` / `cost_usd` / `tokens` fields are `null` for jobs
without a document or whose document has been deleted. For a live feed of state
changes, use `GET /api/events` (§1.8.5) rather than polling this endpoint.

## 1.8.1 Job task names — `GET /api/jobs/task-names`

A plain JSON array of the distinct `task_name` values present in the queue,
ordered alphabetically — e.g. `["library.jobs.poll_email_inbox",
"library.jobs.process_document", …]`. Used to populate the Jobs view's
task-type filter dropdown without the client inferring the set from a partial
result window.

## 1.8.2 Re-extraction — `POST /api/documents/{id}/extract`

Queues the W6 metadata-extraction task for one document and returns
**`202`** with `{"queued": true, "job_id": <procrastinate job id>}` —
the work happens in the background worker. Works on documents in any
state (including already `indexed`); the run honours
`extra["user_edited_fields"]` (user edits are never overwritten) and
never removes tags. `404` for unknown or deleted documents. Track the
outcome via the document's `extraction` provenance block and
`extraction_*` audit events (GET detail, 1.4) or `GET /api/jobs`.
Extraction can also be *skipped* (disabled, missing API key, daily
budget reached) — that is recorded as an `extraction_skipped` event,
not an error.

## 1.8.3 Mark verified — `POST /api/documents/{id}/verify`

Sets `review_status = verified` and records a `review_verified` ingestion
event. Returns the updated document detail (`200`). `404` for unknown or
deleted documents. Auth + CSRF apply.

Use this after reviewing a document's metadata in the detail view and
confirming it is correct. The `review_status` can return to `needs_review`
if extraction is re-run and new findings are produced.

## 1.8.4 Taxonomy — `GET /api/kinds`, `/api/senders`, `/api/recipients`, `/api/tags`

Plain JSON arrays for filter options and edit forms; the same data the
MCP `list_*` tools return (one shared service, `library.taxonomy`).
Counts exclude soft-deleted documents; zero-count entries are included.

- `GET /api/kinds` → `[{slug, name, document_count}, …]`, ordered by
  slug. The seeded set plus any kinds created via `POST /api/kinds`.
- `GET /api/senders` → `[{id, name, document_count}, …]`, ordered by
  name.
- `GET /api/recipients` → `[{id, name, document_count}, …]`, ordered by
  name.
- `GET /api/tags` → `[{slug, name, document_count}, …]`, ordered by
  name.

### 1.8.4.1 Create a kind — `POST /api/kinds`

Adds a new document kind so users aren't limited to the seeded set (e.g.
a `quote` kind alongside `invoice`/`receipt`). Available to any
authenticated user, mirroring how senders/recipients/tags are created
inline through a document edit.

- **Body.** `{"name": "Quote"}` — the human display name (1–255 chars).
- **Slug.** Derived from the name (`slugify`: lowercased, non-alphanumeric
  runs → hyphens), so `"Bank statement"` → slug `bank-statement`.
- **Casing.** The stored display name is standardised to sentence case
  (first letter upper, rest lower, internal whitespace collapsed) to match
  the seeded names — `"BANK STATEMENT"` → `"Bank statement"`.
- **Exact dedupe.** A name/slug that matches an existing kind
  case- and whitespace-insensitively returns that kind with **`200`**
  (no duplicate row created).
- **Near-duplicate guard.** A name within a small edit distance of an
  existing kind (e.g. `"Quotes"` vs `"Quote"`) is refused with **`409`**;
  the flat body carries `detail`, `existing_slug`, and `existing_name` so
  the client can point the user at the existing kind.
- **Success.** A genuinely new kind is created and returned as
  `{slug, name}` with **`201`**.

## 1.8.5 Live job events (SSE) — `GET /api/events`

A [Server-Sent Events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events)
stream of document-pipeline state changes, used by the web app to drive the
navbar running-jobs indicator, toasts, the live Jobs view, and the live status
badges on the document list and detail pages — all without polling. (Each event
also bumps the jobs Pinia store's `lastEvent`, which those views watch to
refetch or patch themselves; document-less system tasks emit no SSE event, so the
Jobs view polls for them while "Show system tasks" is on.)

- **Transport.** The worker emits a Postgres `NOTIFY` on the `library_doc_events`
  channel each time a document changes pipeline stage (`status_changed`) or fails
  (`failed`). The api process runs a single process-wide events broker
  (`library.events_broker`) that holds *one* `LISTEN` connection for its whole
  lifetime and fans each notification out in-process to every connected client;
  this endpoint just drains a per-client queue. SSE Postgres usage is therefore
  capped at one connection per process, not one per open tab. The worker→api hop
  crosses processes via Postgres itself, so both must point at the same database
  (they do in the standard compose deployment).
- **Auth.** Same session cookie as the rest of `/api` (§1.9). A GET is CSRF-safe,
  so a browser `EventSource` — which cannot send headers — authenticates with the
  cookie alone. Unauthenticated requests get `401` before the stream opens.
- **Wire format.** Named SSE events:
  - `event: document` — `data` is JSON `{document_id, event, status, title}`,
    where `status` is the stage the document just entered. A document enters at
    `received`, so the stages actually emitted are `ocr`→`extract`→`markdown`→
    `embed`→`indexed`, plus `failed` on a terminal error.
  - keep-alive comments every ~15 s so idle connections and proxies don't time
    out. The response sets `X-Accel-Buffering: no` to disable proxy buffering.
- **Lifecycle.** The browser's `EventSource` reconnects automatically; on
  disconnect the server drops only that client's in-process queue — the shared
  `LISTEN` connection lives on for the other clients. Events are not replayed on
  reconnect — fetch `GET /api/jobs` for the current snapshot.

## 1.9 Authentication

Two interchangeable credentials, checked by a single dependency on every
`/api` route:

1. **Browser session cookie** — set by `POST /api/auth/login`.
2. **Bearer API token** — `Authorization: Bearer library_…`, for scripts,
   shortcuts, and the MCP server.

When an `Authorization: Bearer` header is present it is authoritative: the
token is validated and cookies are ignored.

Passwords are hashed with **Argon2id** (pwdlib). Accounts are managed from
the host with the bundled CLI — there is no signup endpoint:

```console
library user add anna --display-name "Anna"   # prompts for password
library user passwd anna
library user disable anna                      # also revokes all sessions/tokens
library user list
```

### 1.9.1 Sessions — `POST /api/auth/login`

JSON body `{"username": "...", "password": "..."}`. On success, `200` with
`{id, username, display_name, preferences}` and two cookies:

| Cookie | Flags | Purpose |
|--------|-------|---------|
| `library_session` | `HttpOnly; Secure; SameSite=Lax; Path=/` | Opaque 256-bit session token; only its SHA-256 hash is stored server-side |
| `library_csrftoken` | `Secure; SameSite=Lax; Path=/` (readable by JS) | CSRF double-submit value |

Wrong username, wrong password, and disabled accounts all return the same
generic `401` (`{"detail": "invalid credentials"}`) — no account
enumeration. The `Secure` flag follows the `LIBRARY_COOKIE_SECURE` setting
(default `true`; set `false` only for plain-HTTP dev).

Sessions live in Postgres and expire after `LIBRARY_SESSION_TTL_DAYS`
(default 30) of inactivity, with **sliding expiry**: any authenticated use
pushes the expiry forward (the refresh is write-throttled to at most once
per ~5 minutes). `POST /api/auth/logout` deletes the session row — the
cookie is dead server-side immediately — and clears both cookies.

`GET /api/auth/me` returns `{id, username, display_name, preferences}` for
the authenticated user (either credential). The login response (`POST
/api/auth/login`) returns the same shape. `preferences` is
`{"dashboard_fields": [...], "background_tone": "neutral", "tile_preview": "full_width"}` —
the resolved preference set (defaults filled; see 1.10).

### 1.9.2 CSRF (cookie requests only)

State-changing requests (`POST`/`PATCH`/`PUT`/`DELETE`) authenticated by
the **session cookie** must echo the CSRF cookie in a header:

```
X-CSRF-Token: <value of the library_csrftoken cookie>
```

Missing or mismatched header → `403`. Exempt: `GET`/`HEAD`/`OPTIONS`,
requests carrying an `Authorization: Bearer` header, and
`POST /api/auth/login` itself.

### 1.9.3 API tokens

`POST /api/auth/tokens` with `{"name": "ios-shortcut"}` returns `201`:

```json
{"id": 4, "name": "ios-shortcut", "token": "library_3q2…", "created_at": "…"}
```

**The `token` secret is shown exactly once.** Only its SHA-256 hash is
stored; it cannot be retrieved again — lose it, revoke it, make a new one.
`GET /api/auth/tokens` lists your tokens as
`[{id, name, created_at, last_used_at, revoked_at}, …]` (never secrets;
`last_used_at` updates are throttled to ~5-minute granularity).
`DELETE /api/auth/tokens/{id}` revokes the token (sets `revoked_at`;
takes effect immediately) and returns `204`; tokens belonging to other
users `404`. Tokens do not expire — revocation is the lifecycle.

Usage:

```console
curl -H "Authorization: Bearer library_3q2…" \
  "https://library.example.org/api/documents?q=rekening"

curl -H "Authorization: Bearer library_3q2…" \
  -F "file=@scan.pdf" https://library.example.org/api/documents
```

Bearer requests are CSRF-exempt (the header cannot be set cross-site).
Revoked or unknown tokens, and tokens of disabled users, get `401`.

## 1.10 Settings — `GET /api/settings`, `PUT /api/settings`, `PUT /api/settings/appearance`, `PUT /api/settings/notifications`

Per-user preferences: which metadata fields appear on the dashboard tiles, the
page-canvas tone behind them, how each tile previews the document's first page,
and Pushover notification settings (incl. email forwarding addresses). Auth and
CSRF rules are identical to the rest of `/api` (§1.9). All preferences live in
one JSONB `preferences` blob on the user row; writes are split per concern
(fields vs appearance vs notifications) so each Settings tab saves
independently, and every write preserves the sibling keys.

### 1.10.1 `GET /api/settings`

Returns the resolved preference set for the authenticated user. If the
user has never saved preferences, the **default set** is returned (no
`404` or empty body).

```json
{"dashboard_fields": ["kind", "sender", "tags", "date", "language", "status"], "background_tone": "neutral", "tile_preview": "full_width", "notifications": {"enabled": false, "pushover_app_token_set": false, "pushover_user_key_set": false, "pushover_device": null, "events": [], "email_forward_addresses": []}}
```

### 1.10.2 `PUT /api/settings`

Body: `{"dashboard_fields": [...]}`. Persists the list and returns the
**full** resolved preference set (same shape as GET, incl. `background_tone`).
Auth + CSRF apply.

**Valid field keys** (the 8 selectable fields):

| Key | What it controls on the tile |
|-----|-------------------------------|
| `kind` | Document type tag (blue) |
| `sender` | Correspondent name |
| `tags` | Document tags row (capped at 4 + "+N" overflow) |
| `date` | Document date |
| `language` | Language tag (grey) |
| `status` | Status tag (red/yellow; only shown when non-indexed) |
| `amount` | Financial total (`amount_total` + `currency`, formatted) |
| `file_type` | Derived file type label (PDF / Image / Text / File) |

**Default set** (what new users see): `kind`, `sender`, `tags`, `date`,
`language`, `status`.

**Tolerant validation.** Unknown keys in `dashboard_fields` are silently
dropped — the server returns `200` with the cleaned set, never `422`.
Duplicates are also removed. This means a hand-edited database row or a
client sending a renamed field key can never break the dashboard.

**Explicit empty list** is honoured: `{"dashboard_fields": []}` is valid
and results in tiles showing only the title and thumbnail. The absent-key
rule applies only on read (`GET` / login / `GET /api/auth/me`): if the
`dashboard_fields` key is missing from the stored blob, defaults are
filled in; if the key is present but the list is empty, the empty list
is returned as-is.

### 1.10.3 `PUT /api/settings/appearance`

Body: `{"background_tone": "<tone>", "tile_preview": "<mode>"}`. Persists both
appearance settings and returns the full resolved preference set (same shape
as GET). Auth + CSRF apply. The tone applies to the light-mode page background
only — dark mode keeps its `gray-900` canvas.

**Valid tones:** `neutral` (default — `gray-200`), `light` (`gray-100`,
the original airier canvas), `soft`, `slate`, `sand`, `mist`. The token is
a name, not a colour: the frontend (`assets/main.css`) owns the actual hex
for each tone, so the palette can be retuned without a schema or data
migration.

**Valid tile previews:** `full_width` (default — the first-page thumbnail
fills the tile width, top-aligned, lower part of the page cropped) and
`whole_page` (the entire first page shown letterboxed inside the tile). The
token names a render mode; the frontend owns the CSS object-fit for each.

**Tolerant validation.** An unknown tone resolves to `neutral` and an unknown
tile preview to `full_width` — `200` with the default, never `422` — matching
`dashboard_fields`. `tile_preview` is also optional in the body (defaults to
`full_width`), so a client sending only `background_tone` still succeeds. On
read, absent keys resolve to their defaults.

### 1.10.4 `PUT /api/settings/notifications`

Per-user Pushover push notifications and the email addresses you forward
documents from. Auth + CSRF apply. Returns the full resolved preference set
(same shape as GET).

**Notification model.** Each user supplies their **own** Pushover application
token and user key (register a free app at pushover.net). Notifications target
the document **owner** (`uploader_id`): on completion the worker sends a single
push (the `needs_review` message when the document was flagged and the owner
subscribed, otherwise `document_success`); a failure sends `processing_error`;
and an ingest-time `duplicate` sends from the ingest path — each only if the
owner subscribed to that event. See
[jobs-and-notifications.md](jobs-and-notifications.md) §1.5 for the full rule.
Documents with no owner (consume-folder, paperless import) notify no one.

**Request body:**

```json
{
  "enabled": true,
  "pushover_app_token": "axxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "pushover_user_key": "uxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "pushover_device": "iphone",
  "events": ["document_success", "processing_error"],
  "email_forward_addresses": ["me@example.com"]
}
```

**Event keys:** `document_success`, `processing_error`, `needs_review`,
`duplicate`. New users start with **none** selected (opt-in). Unknown keys are
dropped (`200`, never `422`).

**Secrets are write-only.** The read model returns booleans
`pushover_app_token_set` / `pushover_user_key_set` and the non-secret
`pushover_device` / `events` / `email_forward_addresses` — never the raw token
or key. On write, an **omitted or blank** `pushover_app_token` /
`pushover_user_key` keeps the stored value unchanged, so saving only `events`
never wipes credentials.

**Validation on save.** When `enabled` is `true`, both credentials must be
present (`422` otherwise) and are verified against Pushover's `users/validate`
endpoint; a typo returns `422` with the Pushover error in `detail` rather than
silently dropping every future push. When `enabled` is `false`, no Pushover call
is made.

**`email_forward_addresses`** are lowercased, de-duplicated, and trimmed.
Email-in attributes an incoming message to the user whose list contains the
sender's address (see [ingestion.md](ingestion.md), "Email-in").

The GET / login / `/api/auth/me` read model embeds a `notifications` object,
e.g. `{"enabled": false, "pushover_app_token_set": false,
"pushover_user_key_set": false, "pushover_device": null, "events": [],
"email_forward_addresses": []}`.

## 1.11 Ask — `POST /api/ask`

Answer a natural-language question about the archive, grounded in retrieved
documents. The narrative — architecture, the two question classes, config, cost,
and conversational threading — is in [ask.md](ask.md); this is the wire contract.

**Request:**

```json
{
  "question": "<1..1000 chars>",
  "thread_id": 42,
  "images": [{ "media_type": "image/png", "data": "<base64, no data: prefix>" }]
}
```

`thread_id` is optional. Omit it to start a new conversation; supply it to
continue an existing one. Auth + CSRF apply (it is a `POST`).

`images` is optional (W11): up to **5** base64 attachments for the multimodal
model (`ask_model` = `claude-sonnet-4-6`). Each has a `media_type` of
`image/png`, `image/jpeg`, `image/gif`, or `image/webp` and base64 `data` with
no `data:` prefix. They become image content blocks on the question turn (and
persist in `ask_turns.messages` for replay). `422` if more than 5 images or an
unsupported `media_type`.

**Response `200`:**

```json
{
  "answer": "Yes — your contract grants a travel allowance of €0.21/km [#42].",
  "citations": [
    {"document_id": 42, "title": "Employment contract", "page_number": 3}
  ],
  "used_tools": ["semantic_search"],
  "cost_usd": 0.0031,
  "thread_id": 1
}
```

- `answer` — prose, grounded only in retrieved documents; it says plainly when
  the archive does not contain the answer (then `citations` is empty).
- `citations` — documents the answer relied on (`document_id`, `title`,
  `page_number`); link these to `GET /api/documents/{id}`.
- `used_tools` — which retrieval tools the engine invoked
  (`semantic_search`, `query_documents`, `compare_to_series`).
- `cost_usd` — estimated answer cost for this turn (recorded in `ask_turns`,
  not gated; thread total = sum of its turns).
- `thread_id` — the conversation thread this turn belongs to (new or existing).

**Errors:** `503` when no Anthropic API key is configured; `422` when the
question is empty or too long; `404` when `thread_id` does not exist or belongs
to another user.

## 1.12 Ask threads

Conversation threads persist server-side. All thread endpoints enforce
ownership: a thread belonging to another user returns `404` (not `403`) to
avoid disclosing thread existence.

### `GET /api/ask/threads`

List the authenticated user's conversations, newest-updated first.

```json
[
  {
    "id": 1,
    "title": "Do I have a travel allowance in my job contract?",
    "created_at": "2026-06-22T10:00:00Z",
    "updated_at": "2026-06-22T10:05:00Z",
    "turn_count": 3,
    "total_cost_usd": 0.012
  }
]
```

### `GET /api/ask/threads/{id}`

Full thread detail: metadata and every turn in chronological order.
The raw replay `messages` blob is not returned to the client.

```json
{
  "id": 1,
  "title": "Do I have a travel allowance in my job contract?",
  "turns": [
    {
      "id": 1,
      "query": "Do I have a travel allowance in my job contract?",
      "answer": "Yes — your contract grants …",
      "citations": [{"document_id": 42, "title": "Employment contract", "page_number": 3}],
      "used_tools": ["semantic_search"],
      "cost_usd": 0.0031,
      "created_at": "2026-06-22T10:00:00Z"
    }
  ]
}
```

`404` if the thread does not exist or belongs to another user.

### `DELETE /api/ask/threads/{id}`

Delete a conversation. Cascades to all its turns. Returns `204` on success;
`404` if the thread does not exist or belongs to another user.

## 1.13 Document series — `GET /api/documents/{id}/series`

Returns statistical information about the recurring-document series this
document belongs to, and where this specific document sits within it. The
series is identified automatically from the document's own `sender_id` and
`kind_id`; the document itself is the reference point.

This endpoint supplies the data for the trend widget on the document detail
view. See [ask.md §1.7](ask.md) for the series detection and statistics design.

**Response `200` — `status:"ok"`:**

```json
{
  "status": "ok",
  "sender": "Vattenfall",
  "kind": "utility-bill",
  "sender_id": 7,
  "kind_id": 2,
  "currency": "EUR",
  "other_currencies": [],
  "cadence": "monthly",
  "count": 7,
  "description": "Energy bills have crept up about 12% over the past year, peaking in winter.",
  "mean": "145.00",
  "median": "142.10",
  "stdev": "8.20",
  "min": "131.00",
  "max": "159.40",
  "reference": {
    "value": "151.20",
    "delta": "+9.10",
    "vs_median_pct": "+6.4%",
    "z_score": 1.11,
    "verdict": "higher"
  },
  "trend": { "direction": "rising", "change_pct": "+12.0%" },
  "year_over_year": {
    "prior_value": "138.40",
    "change_pct": "+9.2%",
    "document_id": 41
  },
  "document_ids": [12, 19, 27, 33, 41, 55, 88],
  "points": [
    {"date": "2025-06-15", "amount": "138.40", "document_id": 41, "title": "June 2025 bill"},
    {"date": "2026-05-15", "amount": "151.20", "document_id": 88, "title": "May 2026 bill"}
  ]
}
```

Fields:

- `status` — `"ok"` when the series has enough members; `"insufficient"` otherwise (see below).
- `sender`, `kind`, `currency` — the resolved series identity and reported currency bucket.
- `sender_id`, `kind_id` — numeric ids of the resolved series (used as a stable key on `/charts`).
- `description` — a cached, LLM-generated one- or two-sentence prose summary of the
  series, precomputed in the background (see [ask.md §1.7](ask.md)). Absent until the
  first description has been generated for the series.
- `other_currencies` — currencies present in the series that are not being reported.
- `cadence` — inferred recurrence: `monthly`, `quarterly`, `yearly`, or `irregular`.
- `count`, `mean`, `median`, `stdev`, `min`, `max` — distribution stats over `amount_total`
  within the currency bucket. Money values are JSON strings (decimal precision preserved).
- `reference` — where this document's amount sits: `value`, `delta` (vs median),
  `vs_median_pct`, `z_score` (null when stdev is 0), and a `verdict` of
  `higher`, `typical`, or `lower`.
- `trend` — `direction` (`rising`/`falling`/`flat`) and `change_pct` (first→last).
- `year_over_year` — the series member closest to 12 months prior (`prior_value`,
  `change_pct`, `document_id`), or `null` when no match exists.
- `document_ids` — ids of the series members that contributed to the stats (capped at 25).
- `points` — all dated, amount-bearing series members in chronological order, each with
  `date` (ISO), `amount` (string), `document_id`, and `title` (the document's title, or
  `null`). Use `document_id` to highlight the current document in the chart and to link
  each point to its document; `title` labels the citation.

**Response `200` — `status:"insufficient"`:**

Returned when the document has no `sender` or `kind`, or when the series has
fewer than `LIBRARY_SERIES_MIN_DOCUMENTS` members (default 3). The UI should
hide the trend widget rather than showing an error.

```json
{"status": "insufficient", "count": 1, "document_ids": [88]}
```

`count` is the number of series members found (0 when the document has no sender or kind).

**Errors:** `404` when the document does not exist or is soft-deleted.

## 1.14 Charts — `GET /api/charts`

Enumerates every recurring `(sender, kind)` series with enough amount-bearing
documents to summarise (at least `LIBRARY_SERIES_MIN_DOCUMENTS`, default 3), and
returns each one fully summarised. This backs the `/charts` aggregate view.

**Response `200`:**

```json
{
  "series": [
    {
      "status": "ok",
      "sender": "Vattenfall",
      "kind": "utility-bill",
      "sender_id": 7,
      "kind_id": 2,
      "currency": "EUR",
      "count": 7,
      "description": "Energy bills have crept up about 12% over the past year…",
      "median": "142.10",
      "trend": { "direction": "rising", "change_pct": "+12.0%" },
      "document_ids": [12, 19, 27, 33, 41, 55, 88],
      "points": [
        {"date": "2025-06-15", "amount": "138.40", "document_id": 41, "title": "June 2025 bill"}
      ]
    }
  ]
}
```

Each entry has the **same shape** as the `status:"ok"` body of
`GET /api/documents/{id}/series` (with `points` always included). Series whose
dominant currency bucket is too small are omitted, so every returned entry is
`status:"ok"`. Entries are ordered by document count (busiest series first).
There is no per-document reference point here, so `reference`/`year_over_year`
are anchored on the latest member. Use `sender_id`-`kind_id`-`currency` as a
stable key.

## 1.15 Series membership overrides — `/api/series/{sender_id}/{kind_id}/members`

Series are computed on the fly (no membership table). These two endpoints let a
user **manually correct** that computation and have the correction persist:
`pin` a document the grouping missed, or `exclude` one it wrongly grouped in.
Overrides are stored in `series_membership_overrides`, keyed by the series
identity `(sender_id, kind_id, currency)` plus `document_id`, and applied on
every future `summarize_series` call (so both `GET /api/documents/{id}/series`
and `GET /api/charts` reflect them). They mirror the per-document extraction
`corrections` precedent, but as a first-class table so the LLM series matcher
can read accumulated examples as hints.

A `(series, document)` pair is in one of three states; both endpoints are
idempotent toggles between them:

- `pinned` — a `pin` override exists (force-include).
- `excluded` — an `exclude` override exists (force-remove).
- `cleared` — no override; the document follows the natural grouping.

| Method | Path | Effect |
|--------|------|--------|
| POST   | `/api/series/{sender_id}/{kind_id}/members` | Add: clear an existing `exclude`, else `pin`. Body `{"document_id": int}` |
| DELETE | `/api/series/{sender_id}/{kind_id}/members/{document_id}` | Remove: clear an existing `pin`, else `exclude` |

Both accept an optional `?currency=` query parameter (the series currency
bucket from the chart tile / document; omit it for the `NULL` bucket). Both
return the resulting state:

```json
{"state": "pinned", "sender_id": 7, "kind_id": 2, "currency": "EUR", "document_id": 88}
```

**Cross-currency pins → FX conversion.** A pinned document whose own currency
differs from the series is converted into the series currency using the seeded
`fx_rates` reference table (base = USD), **date-aware**: the rate is the one
with the greatest `as_of` on-or-before the document's date (falling back to the
earliest). The seed is a researched *approximate* yearly snapshot (2015–2026)
for the common currencies (EUR, GBP, CHF, JPY, CAD, AUD, SEK, NOK, DKK); add
rows to refine it. A pinned document with no amount, or in a currency absent
from `fx_rates`, stays out of the computed stats (it cannot contribute a
meaningful point) and the omission is logged.

**Notes & errors.** Excluding enough members can drop a series below
`LIBRARY_SERIES_MIN_DOCUMENTS`, after which it reports `status:"insufficient"` —
this is intended. With no overrides, output is byte-for-byte what it was before
(additive, backward-compatible). `404` when the sender, kind, or document does
not exist; `401` when unauthenticated.

## 1.16 Projects — `/api/projects`

First-class **collections**: a many-to-many grouping of documents
(`projects` + `document_projects` tables, migration 0011), mirroring the
tags pattern but with their own CRUD surface, descriptions, and a
soft-archive state. A document's project membership is edited through
`PATCH /api/documents/{id}` (the `projects` field, §1.5) and surfaced as the
`projects` array on every document list/detail item; documents are also
filterable by `?project=<slug>` (§1.3.1).

**Slugs are stable.** `POST` derives a slug from the name (or accepts an
explicit, normalised `slug` override); `PATCH` never changes it, so inbound
links and the `?project=` filter survive renames. Counts exclude soft-deleted
documents and include zero-count projects.

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/projects` | All projects ordered by name. `?include_archived=true` to include archived ones (hidden by default). Open to all authenticated users. |
| POST | `/api/projects` | **Admin only** (`403` otherwise). Body `{name, slug?, description?}`. `201`; `409` if the slug already exists. |
| GET | `/api/projects/{slug}` | One project; `404` if unknown. Open to all authenticated users. |
| PATCH | `/api/projects/{slug}` | **Admin only** (`403` otherwise). Body `{name?, description?, archived?}`; only present fields change. The slug is **immutable**. `archived: true/false` toggles `archived_at`. `404` if unknown. |
| DELETE | `/api/projects/{slug}` | **Admin only** (`403` otherwise). Hard-delete; `204`. Memberships cascade away (`document_projects`), the **documents themselves are kept**. `404` if unknown. |

Projects are a global, shared taxonomy, so mutating them is restricted to
admins (reads stay open). See [admin.md](admin.md).

**Project object** (every endpoint returns this shape; `GET /api/projects`
returns an array of them):

```json
{
  "id": 3,
  "slug": "house-purchase",
  "name": "House purchase",
  "description": "Mortgage, survey, and notary paperwork",
  "archived": false,
  "document_count": 12
}
```

`document_count` is the number of non-deleted documents in the project.
Auth + CSRF apply exactly as elsewhere (§1.9).

## 1.17 Notes — `/api/notes`

In-app **note authoring**: compose a Markdown note directly in Library and it
becomes a first-class document (`source = "note"`, `mime_type =
"text/markdown"`) that flows through the normal pipeline — one
born-digital `DocumentPage`, no OCR/vision API call, with metadata still
auto-extracted from the body. Unlike an upload, a note is **edited in place**
(the same document row) with a version-history snapshot recorded on every edit,
and is **exempt from content dedup** (its `sha256` is a salted digest), so two
identical notes — or a note edited back to an earlier body — never collide. See
[ingestion.md](ingestion.md) "Notes" for the storage/dedup mechanics. Auth +
CSRF apply exactly as elsewhere (§1.9).

| Method | Path | Notes |
|--------|------|-------|
| POST | `/api/notes` | Body `{title, body_markdown}`. Creates the note and queues processing; `201` with the full document detail (same shape as `GET /api/documents/{id}`, §1.4). |
| PATCH | `/api/notes/{id}` | Body `{title?, body_markdown?}`; only present fields change. Snapshots the prior (title, body) into history, applies the edit, and (on a body change) re-runs extraction + markdown (which re-embeds). Returns the updated detail. `404` for unknown, deleted, or **non-note** documents. |
| GET | `/api/notes/{id}/versions` | The note's version history, **newest first**: `[{version_no, title, body, created_at}, …]`. `404` for non-note documents. |
| POST | `/api/notes/{id}/versions/{version_no}/restore` | Snapshots the current state, then re-applies the chosen version's title + body (a restore is itself an edit, so it can be undone). Returns the updated detail. `404` for an unknown note **or** unknown version number. |

**Title is locked.** A note's `title` is added to `extra["user_edited_fields"]`
on create, so re-extraction (and the re-extraction triggered by every edit)
never overwrites it; the body still drives the auto-extracted summary, topics,
tags, and kind. Each create/edit/restore also appends an ingestion event
(`received` / `note_edited` / `note_restored`) to the document's audit trail.

**Create body:**

```json
{"title": "Mortgage call notes", "body_markdown": "# Call with broker\n\n- rate 3.9% …"}
```

## 1.18 Admin — `/api/admin`

Admin-only context and user management. Every endpoint requires the **admin**
role (`require_admin`): anonymous → `401`, non-admin → `403`. Full design notes
in [admin.md](admin.md).

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/admin/system` | App version + git sha, redacted operational config, deployment topology, and live DB stats (documents by status, users, job-queue depth, total extraction spend). |
| GET | `/api/admin/architecture` | `docs/architecture.md` + `docs/ingestion.md` as `{docs: [{name, title, markdown}]}` (rendered client-side). |
| GET | `/api/admin/coverage` | Backend/frontend coverage vs gate from the CI-baked summary: `{available, backend, frontend, generated_at, git_sha}`; `available: false` when no summary is baked in. |
| GET | `/api/admin/users` | Every user: `[{id, username, display_name, is_admin, is_active, created_at}]`. |
| POST | `/api/admin/users` | Body `{username, password, display_name?, is_admin?}`. `201`; `409` if the username exists. |
| PATCH | `/api/admin/users/{id}` | Body `{is_admin?, is_active?}`. Promote/demote, activate/deactivate. `404` unknown; `409` if it would remove the **last active admin**. Deactivating also revokes the user's sessions and tokens. |
| PATCH | `/api/admin/recipients/{id}` | Rename or merge a recipient. See §1.18.1. |
| DELETE | `/api/admin/recipients/{id}` | Delete a recipient, reassigning its documents. See §1.18.2. |

The system `config` view exposes only a curated, secret-free subset of settings
— never API keys, passwords, or internal URLs.

### 1.18.1 `PATCH /api/admin/recipients/{id}` — rename / merge

Body `{name, merge?}` (`name` trimmed, ≤255 chars; `merge` default `false`).
Recipients are a shared taxonomy, so this is admin-only.

- **`200`** → `{id, name}`. The name was updated in place. The collision check is
  **case-insensitive but excludes the recipient itself**, so a pure casing change
  (`john` → `John`) succeeds here.
- **`400`** → name was blank after trimming.
- **`404`** → no such recipient.
- **`409`** → the (case-insensitive) name matches **another** recipient and
  `merge` was not set. Body:

  ```json
  {"detail": "…", "target_id": 7, "target_name": "John", "target_document_count": 4}
  ```

  The conflict fields sit at the **top level** alongside the human-readable
  `detail` string (a flat body, returned via `JSONResponse` so FastAPI does not
  nest them). Re-send with `{name, "merge": true}` to reassign this recipient's
  documents onto `target_id`, delete this recipient, and return the surviving
  target `{id, name}` (`200`).

### 1.18.2 `DELETE /api/admin/recipients/{id}` — reassign-then-delete

Deletes a recipient, first moving its documents off it. The `reassign_to` query
param is **three-state**:

| `reassign_to` | Effect |
|---|---|
| *omitted* | Zero-document recipient → deleted (`204`). In-use recipient → `409` (see below). |
| `=<id>` | Move this recipient's documents to recipient `<id>`, then delete (`204`). |
| `=` (empty / `null`) | Clear the recipient on its documents (set NULL), then delete (`204`). |

All reassignments move **every** document, soft-deleted included. Responses:

- **`204`** → deleted (with documents reassigned/cleared as above).
- **`400`** → `reassign_to` equals the recipient being deleted (self-reassign).
- **`404`** → unknown recipient, or unknown `reassign_to` target.
- **`409`** → recipient still has documents and `reassign_to` was omitted. Body
  is flat (top-level fields, returned via `JSONResponse`):
  `{"detail": "…", "document_count": 4}`.
- **`422`** → `reassign_to` was neither an integer, empty, nor `null`.
