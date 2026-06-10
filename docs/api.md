# 1. REST API

**Status:** active. **Last updated:** 2026-06-10.

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
| POST   | `/api/documents` | Upload a file for ingestion |
| GET    | `/api/documents` | List / search documents |
| GET    | `/api/documents/{id}` | Full document detail |
| PATCH  | `/api/documents/{id}` | Edit metadata |
| DELETE | `/api/documents/{id}` | Soft-delete |
| GET    | `/api/documents/{id}/original` | Download the original file |
| GET    | `/api/documents/{id}/searchable.pdf` | Download the OCR searchable PDF |
| GET    | `/api/documents/{id}/thumbnail` | First-page WebP thumbnail |
| GET    | `/api/jobs` | Recent background jobs |

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
| `tag` | string, repeatable | Tag slug; repeating the parameter ANDs them (`?tag=energie&tag=wonen` requires both) |
| `language` | enum | `nld` / `eng` / `mixed` / `unknown` |
| `status` | enum | `received` / `ocr` / `extract` / `indexed` / `failed` |
| `date_from`, `date_to` | date | Inclusive bounds on `document_date` |
| `source` | enum | `upload` / `consume` / `email` / `api` / `mcp` / `import` |
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
      "tags": [{"slug": "energie", "name": "Energie"}],
      "document_date": "2026-05-15", "language": "nld",
      "status": "indexed", "mime_type": "application/pdf",
      "page_count": 2, "created_at": "2026-06-10T12:00:00Z",
      "has_searchable_pdf": true, "has_thumbnail": true,
      "snippet": "uw <b>rekening</b> voor mei … totaal",
      "rank": 0.31
    }
  ],
  "total": 1, "limit": 25, "offset": 0
}
```

`total` is the filtered count before pagination. `snippet` and `rank` are
only present (non-null) when `q` is given. Tags are sorted by slug.

### 1.3.3 Search semantics

- `q` is parsed with `websearch_to_tsquery` and matched against **both**
  generated tsvector columns — `search_vector_nl` (`dutch` config) and
  `search_vector_en` (`english` config), OR-combined. Stemming therefore
  works in both languages: `q=rekening` finds "rekeningen", `q=policy`
  finds "policies".
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
| `tags` | **Full replacement** list of slugs; unknown slugs are created; `[]` clears; `null` is rejected |
| `language` | `nld` / `eng` / `mixed` / `unknown`; `null` rejected |
| `amount_total` | Decimal as string or number; `null` clears |
| `currency` | 3-letter code, normalized to upper case; `null` clears |

Every edited field is appended to `extra["user_edited_fields"]` (mapped to
storage names: `kind_slug`→`kind_id`, `sender`→`sender_id`). Re-extraction
(W6) honours this list and never overwrites user-edited fields. An
ingestion event `user_edited` is recorded with the changed field names.
Returns the updated document detail.

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
- `GET /api/documents/{id}/thumbnail` — first-page thumbnail,
  `image/webp`, ~480 px wide. Generated by a background job after OCR;
  `404` until it exists (and always for plain-text documents).
  `has_thumbnail` in list/detail responses reflects file existence.

## 1.8 Jobs — `GET /api/jobs`

Most recent background jobs (newest first) from the Procrastinate queue:
`[{id, status, task_name, attempts, scheduled_at, document_id}, …]`.
`limit` 1–500, default 50.

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
`{id, username, display_name}` and two cookies:

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

`GET /api/auth/me` returns `{id, username, display_name}` for the
authenticated user (either credential).

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
