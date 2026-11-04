# MCP server

**Status:** active. **Last updated:** 2026-11-04 (slashless `/mcp`, tool titles and annotations, masked errors, id bounds, shared DB pool; error text no longer points at the REST API).
**Last verified:** 2026-11-04 — method: read `mcp_server.py` in full, the `/mcp` wiring in `app.py`, the pool in `db.py`, `taxonomy.list_senders`, the status enum in `models.py` and the MIME list in `ingest.py` against every section; ran `tests/test_mcp.py` and `tests/test_spa.py` (green), which assert the annotations, errors and slashless URL.
**Covers:** src/library/mcp_server.py, src/library/auth/service.py, src/library/db.py, src/library/app.py

Library exposes a [Model Context Protocol](https://modelcontextprotocol.io)
server so LLM clients (Claude Code, Claude Desktop, the Anthropic API MCP
connector, or any MCP-capable agent) can search the archive, read
documents, fetch files, and ingest new documents.

- **Endpoint:** `https://<your-host>/mcp` (Streamable HTTP transport,
  stateless, JSON responses). `/mcp/` with a trailing slash is the same
  endpoint: `/mcp` is rewritten onto it before routing, for every method,
  so no client has to follow a redirect. It is part of the main API
  process — no extra container or port.
- **Auth:** the same opaque bearer API tokens as the REST API
  (`Authorization: Bearer library_…`). Every request without a valid,
  unrevoked token gets `401`. There is no OAuth flow yet; tokens are
  created by logged-in users and revoked individually (see
  [api.md §1.9.3](api.md)).
- **Implementation:** [FastMCP 3.x](https://gofastmcp.com) mounted into
  the FastAPI app at `/mcp` with a shared lifespan
  (`src/library/mcp_server.py`, wired in `src/library/app.py`).

## 1.1 Getting a token

Log in to the web app or REST API and create a token (the secret is shown
exactly once):

```console
curl -s -c cookies.txt -X POST https://library.example.org/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username": "anna", "password": "…"}'

CSRF=$(grep library_csrftoken cookies.txt | awk '{print $NF}')
curl -s -b cookies.txt -X POST https://library.example.org/api/auth/tokens \
  -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
  -d '{"name": "claude-mcp"}'
# → {"id": 4, "name": "claude-mcp", "token": "library_3q2…", "created_at": "…"}
```

Revoke it any time with `DELETE /api/auth/tokens/{id}`; revocation takes
effect immediately (the next MCP request gets `401`). User accounts are
created with the `library user` CLI (`library user add anna`).

## 1.2 Connecting clients

### Claude Code

```console
claude mcp add --transport http library https://library.example.org/mcp \
  --header "Authorization: Bearer library_3q2…"
```

### Anthropic API (MCP connector)

Pass the server in the `mcp_servers` block of a Messages API call:

```json
{
  "type": "url",
  "url": "https://library.example.org/mcp",
  "name": "library",
  "authorization_token": "library_3q2…"
}
```

### claude.ai / Claude Desktop custom connectors

Custom connectors in the claude.ai UI authenticate with OAuth, which
Library does not implement yet, so they cannot connect. Use Claude Code
or the API connector.

### Generic MCP clients

Any client supporting **Streamable HTTP** works: point it at
`https://<host>/mcp` and send the `Authorization: Bearer library_…`
header on every request. The server is stateless (no session affinity
required) and responds with plain JSON. SSE-only (deprecated transport)
clients are not supported.

## 1.3 Tools

All tools require auth.

**Errors.** Expected failures (unknown or deleted document, oversized
file, unsupported type) come back as MCP tool errors whose text is
written for the model and never points at the REST API, which an LLM
connector cannot call; where the user has to act, the text says to open
the document in the Library web app. Invalid arguments (a `document_id`
below 1 or above the Postgres `BIGINT` maximum, a `limit` above 50) are
argument-validation errors naming the bound. Anything unexpected (a bug,
a database failure) is masked to `Error calling tool '<name>'`, so SQL
and schema details never reach a client; the full error goes to the
server log.

**Titles and annotations.** Every tool declares a human-readable `title`
and behaviour hints in `tools/list`. All tools except `ingest_document`
are `readOnlyHint: true`, `openWorldHint: false` (clients such as
ChatGPT ask the user to confirm any tool without `readOnlyHint`).
`ingest_document` is `readOnlyHint: false`, `destructiveHint: false`
(it only adds), `idempotentHint: true` (the same bytes again return the
existing document) and `openWorldHint: false`.

| Tool | Parameters | Returns |
|------|------------|---------|
| `search_documents` | `query?` (websearch syntax, Dutch+English stemming), `kind?` (slug), `sender?` (case-insensitive substring of sender name), `recipient?` (case-insensitive substring of recipient name), `tag?` (slug), `project?` (slug — see `list_projects`), `matter?` (slug — see `list_matters`), `language?` (`nld`/`eng`/`mixed`/`unknown`), `date_from?`, `date_to?` (ISO dates, inclusive, on `document_date`), `limit` (default 10, max 50) | `{results: [{id, title, summary, kind, sender, recipient, tags, topics, projects, matters, document_date, language, status, mime_type, page_count, created_at, updated_at, snippet, rank}], total}` — ranked when `query` is given, newest-first otherwise. Snippets carry `<b>…</b>` match markers. |
| `get_document` | `document_id` | Every field of a search result (above) plus amount, provenance, `has_searchable_pdf`, OCR text (truncated at 20 000 characters with an explanatory note and `ocr_text_truncated: true`) and the audit-trail events `[{event, created_at}]`. `topics` are read-only auto-extracted. |
| `get_document_file` | `document_id`, `variant` = `"original"` (default) or `"searchable_pdf"` | `{filename, mime_type, size_bytes, content_base64}`. Files over 10 MB are refused with an error telling the user to download the file from the web app. A variant that was never produced is an error. |
| `ingest_document` | `filename`, `content_base64` (PDF, JPEG, PNG, HEIC/HEIF, TIFF, plain text, Markdown, DOCX), `source_note?` | `{id, sha256, status, duplicate}`. Runs the same ingestion service as uploads (dedup by SHA-256, OCR + extraction queued); `source` is recorded as `mcp` and the calling token's user as uploader. A `source_note` is stored on the audit trail — but only when the ingest was not a duplicate, since a duplicate creates no new document to attach it to. Content matching a document in the trash is refused until it is restored or purged. |
| `list_kinds` | — | `{kinds: [{slug, name, document_count}]}` |
| `list_senders` | — | `{senders: [{id, name, document_count, colour}]}` — every sender, including those with no documents; `colour` is a chart colour or `null`. |
| `list_recipients` | — | `{recipients: [{id, name, document_count}]}` |
| `list_tags` | — | `{tags: [{slug, name, document_count}]}` |
| `list_projects` | — | `{projects: [{id, slug, name, description, document_count}]}` — archived projects are omitted. Use the slugs as the `project` filter of `search_documents`. |
| `list_matters` | — | `{matters: [{id, slug, name, hint, document_count}]}` — archived matters are omitted. Use the slugs as the `matter` filter of `search_documents`. |
| `library_stats` | — | `{total_documents, by_status, by_kind, ingested_last_7_days, oldest_document_date, newest_document_date}`. `by_status` counts the lifecycle states that have documents (`received`, `ocr`, `extract`, `markdown`, `embed`, `indexed`, `failed`); soft-deleted documents are excluded from every figure. |

`search_documents` shares its query builder (`src/library/search.py`)
with `GET /api/documents`, so full-text matching, stemming and ranking are
the same; see [api.md §1.3.3](api.md) for those semantics. It exposes a
subset of the REST filters (one tag, project or matter per call; sender
and recipient by name substring; no facet, status or review filters, no
`offset`) and returns at most 50 results per call.

## 1.4 Security notes

- **Token = full account access.** MCP tools run as the token's user;
  anything the user can do via REST (read, search, ingest) the connected
  LLM can do too. Document deletion and metadata editing are deliberately
  *not* exposed as MCP tools.
- **Transport:** run behind TLS (reverse proxy). Tokens travel in the
  `Authorization` header on every request.
- **Revocation:** tokens validate against the database on every request
  (hashed at rest, `last_used_at` touched at ~5-minute granularity), so
  revoking a token or disabling a user (`library user disable`) cuts MCP
  access immediately.
- **Auth mechanism:** the bearer check is a FastMCP `TokenVerifier` that
  calls the same `validate_api_token` service as the REST API — one code
  path for credential checks. Requests without a valid token are rejected
  by middleware before any tool code runs, with
  `WWW-Authenticate: Bearer` on the response.
- **Size caps:** ingest respects `LIBRARY_MAX_UPLOAD_BYTES` (default
  100 MB, before base64 expansion); file retrieval is capped at 10 MB to
  keep tool results inside LLM context budgets.
- **Database connections:** tools and the token check take sessions
  from the process-wide pool the REST API uses (5 connections plus 10
  overflow). A client fanning out many parallel calls queues on that
  pool instead of exhausting Postgres connections.
