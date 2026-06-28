# 2. MCP server

Library exposes a [Model Context Protocol](https://modelcontextprotocol.io)
server so LLM clients (Claude Code, Claude Desktop, the Anthropic API MCP
connector, or any MCP-capable agent) can search the archive, read
documents, fetch files, and ingest new documents.

- **Endpoint:** `https://<your-host>/mcp/` (Streamable HTTP transport,
  stateless, JSON responses). It is part of the main API process — no
  extra container or port.
- **Auth:** the same opaque bearer API tokens as the REST API
  (`Authorization: Bearer library_…`). Every request without a valid,
  unrevoked token gets `401`. There is no OAuth flow — Library is a
  family-scale, LAN/reverse-proxy deployment; tokens are created by
  logged-in users and revoked individually (see [api.md §1.9.3](api.md)).
- **Implementation:** [FastMCP 3.x](https://gofastmcp.com) mounted into
  the FastAPI app at `/mcp` with a shared lifespan
  (`src/library/mcp_server.py`, wired in `src/library/app.py`).

## 2.1 Getting a token

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

## 2.2 Connecting clients

### Claude Code

```console
claude mcp add --transport http library https://library.example.org/mcp/ \
  --header "Authorization: Bearer library_3q2…"
```

### Anthropic API (MCP connector)

Pass the server in the `mcp_servers` block of a Messages API call:

```json
{
  "type": "url",
  "url": "https://library.example.org/mcp/",
  "name": "library",
  "authorization_token": "library_3q2…"
}
```

### claude.ai / Claude Desktop custom connectors

Custom connectors in the claude.ai UI authenticate with OAuth, which
Library deliberately does not implement (decision 1.1.4 of the build
plan: hand-rolled token auth, no IdP). Use Claude Code or the API
connector, or front Library with an OAuth-terminating proxy if you need
claude.ai web specifically.

### Generic MCP clients

Any client supporting **Streamable HTTP** works: point it at
`https://<host>/mcp/` and send the `Authorization: Bearer library_…`
header on every request. The server is stateless (no session affinity
required) and responds with plain JSON. SSE-only (deprecated transport)
clients are not supported.

## 2.3 Tools

All tools require auth. Errors (unknown document, oversized file,
unsupported type) come back as MCP tool errors with a human-readable
explanation.

| Tool | Parameters | Returns |
|------|------------|---------|
| `search_documents` | `query?` (websearch syntax, Dutch+English stemming), `kind?` (slug), `sender?` (case-insensitive substring of sender name), `tag?` (slug), `project?` (slug — see `list_projects`), `language?` (`nld`/`eng`/`mixed`/`unknown`), `date_from?`, `date_to?` (ISO dates, inclusive, on `document_date`), `limit` (default 10, max 50) | `{results: [{id, title, summary, kind, sender, tags, topics, projects, document_date, language, snippet, rank}], total}` — ranked when `query` is given, newest-first otherwise. Snippets carry `<b>…</b>` match markers. |
| `get_document` | `document_id` | Full metadata (kind, sender, tags, read-only auto-extracted topics, dates, amount, language, status, provenance) plus OCR text (truncated at 20 000 characters with an explanatory note and `ocr_text_truncated: true`) and the audit-trail events `[{event, created_at}]`. |
| `get_document_file` | `document_id`, `variant` = `"original"` (default) or `"searchable_pdf"` | `{filename, mime_type, size_bytes, content_base64}`. Files over 10 MB are refused with an error pointing at the REST download endpoints. |
| `ingest_document` | `filename`, `content_base64`, `source_note?` | `{id, sha256, status, duplicate}`. Runs the same ingestion service as uploads (dedup by SHA-256, OCR + extraction queued); `source` is recorded as `mcp` and the calling token's user as uploader. A `source_note` is stored on the audit trail. |
| `list_kinds` | — | `{kinds: [{slug, name, document_count}]}` |
| `list_senders` | — | `{senders: [{id, name, document_count}]}` |
| `list_tags` | — | `{tags: [{slug, name, document_count}]}` |
| `list_projects` | — | `{projects: [{id, slug, name, description, document_count}]}` — archived projects are omitted. Use the slugs as the `project` filter of `search_documents`. |
| `library_stats` | — | `{total_documents, by_status, by_kind, ingested_last_7_days, oldest_document_date, newest_document_date}` |

Search behaviour is identical to `GET /api/documents` — both run the same
query builder (`src/library/search.py`); see [api.md §1.3.3](api.md) for
the full search semantics.

## 2.4 Security notes

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
