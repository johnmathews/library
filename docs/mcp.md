# MCP server

**Status:** active. **Last updated:** 2026-11-04 (OAuth sign-in for MCP clients when `LIBRARY_PUBLIC_BASE_URL` is set: discovery documents, `/mcp/authorize|token|register|revoke`, per-client instructions, reverse-proxy bypass paths; static tokens unchanged).
**Last verified:** 2026-11-04 — method: read `mcp_server.py`, `app.py`, `api/oauth.py` and `auth/oauth_provider.py` against §1, §1.2 and §1.4; ran `tests/test_mcp_oauth.py` (discovery URLs, SDK client flow, static tokens with OAuth on and off) and `tests/test_mcp.py`, both green.
**Covers:** src/library/mcp_server.py, src/library/auth/service.py, src/library/auth/oauth_provider.py, src/library/api/oauth.py, src/library/db.py, src/library/app.py

Library exposes a [Model Context Protocol](https://modelcontextprotocol.io)
server so LLM clients (Claude Code, claude.ai and Claude Desktop, ChatGPT,
the Anthropic API MCP connector, or any MCP-capable agent) can search the
archive, read documents, fetch files, and ingest new documents.

- **Endpoint:** `https://<your-host>/mcp` (Streamable HTTP transport,
  stateless, JSON responses). `/mcp/` with a trailing slash is the same
  endpoint: `/mcp` is rewritten onto it before routing, for every method,
  so no client has to follow a redirect. It is part of the main API
  process — no extra container or port.
- **Auth:** every request needs `Authorization: Bearer …` with one of two
  kinds of token, and each acts as one Library user:
  - **OAuth** (claude.ai, ChatGPT, Claude Code): on when
    `LIBRARY_PUBLIC_BASE_URL` is set to the server's public `https://`
    origin. The client discovers Library's authorization server, the user
    signs in to Library and approves the client, and the client receives
    `libmcp_at_…` access tokens (1 hour) and rotating `libmcp_rt_…`
    refresh tokens (30 days). See §1.2 (clients), §1.5 (endpoints) and
    §1.6 (reverse-proxy bypass).
  - **Static API tokens** (`library_…`), the same tokens as the REST API,
    created by a logged-in user and revoked individually (§1.1,
    [api.md §1.9.3](api.md)). They work whether or not OAuth is on.

  A request without a valid token gets `401`. With OAuth on, the 401 says
  where to start: `WWW-Authenticate: Bearer
  resource_metadata="https://<host>/.well-known/oauth-protected-resource/mcp"`.
  With `LIBRARY_PUBLIC_BASE_URL` unset — or set to a plain `http://`
  address other than `localhost`/`127.0.0.1`, which OAuth cannot use
  (the server logs a warning and starts without it) — the 401 is a bare
  `Bearer` and only static tokens work.
- **Implementation:** [FastMCP 3.x](https://gofastmcp.com) mounted into
  the FastAPI app at `/mcp` with a shared lifespan
  (`src/library/mcp_server.py`, wired in `src/library/app.py`); the OAuth
  authorization server is `src/library/auth/oauth_provider.py`, and the
  consent and connected-apps API is `src/library/api/oauth.py`
  ([api.md §1.9.4](api.md)).

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

The URL to give every client is `https://<host>/mcp`. With OAuth on
(`LIBRARY_PUBLIC_BASE_URL` set), a client that supports MCP OAuth needs
nothing else: it registers itself, opens Library in the browser, and the
user signs in **with their own Library account** and approves the client
on Library's consent page (`/oauth/consent`). Each family member connects
their own claude.ai or ChatGPT account this way, and everything the
client does is attributed to whoever approved it. Approved clients are
listed and revoked through the connected-apps API
([api.md §1.9.4](api.md)).

**Not usable end to end yet:** the server side above is in place, but the
web app does not have the consent page yet, so a browser sent to
`/oauth/consent` cannot approve anything. Until it ships, connect with a
static token (Claude Code, the API connector, generic clients).

### claude.ai and Claude Desktop

Settings → Connectors → *Add custom connector*, URL
`https://<host>/mcp`. Leave any OAuth client id/secret fields empty:
Library supports dynamic client registration.

### ChatGPT

In ChatGPT with developer mode enabled, add a custom connector (app) with
URL `https://<host>/mcp` and OAuth authentication; ChatGPT registers
itself and opens the consent page.

### Claude Code

```console
claude mcp add --transport http library https://library.example.org/mcp
```

With OAuth on, Claude Code runs the browser sign-in the first time the
server is used (`/mcp` in a session shows its status) and listens on a
local `http://localhost:<port>/callback`, which the redirect allow-list
accepts on any port. Or keep using a static token — it works with OAuth
on or off:

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

### Generic MCP clients

Any client supporting **Streamable HTTP** works: point it at
`https://<host>/mcp` and either let it run the MCP OAuth flow (§1.5) or
send the `Authorization: Bearer library_…` header on every request. The server is stateless (no session affinity
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

- **Token = full account access.** MCP tools run as the token's user
  (for OAuth, the user who approved the client);
  anything the user can do via REST (read, search, ingest) the connected
  LLM can do too. Document deletion and metadata editing are deliberately
  *not* exposed as MCP tools.
- **Transport:** run behind TLS (reverse proxy). Tokens travel in the
  `Authorization` header on every request.
- **Revocation:** both kinds of token validate against the database on
  every request (hashed at rest, last use touched at ~5-minute
  granularity), so revoking a static token, disconnecting an OAuth client
  (`DELETE /api/oauth/grants/{id}`, which kills its access and refresh
  tokens) or disabling a user (`library user disable`) cuts MCP access
  immediately.
- **Auth mechanism:** FastMCP bearer middleware rejects a request without
  a valid token before any tool code runs. Static tokens go through a
  `TokenVerifier` that calls the same `validate_api_token` service as the
  REST API; with OAuth on, a `MultiAuth` tries the OAuth provider's
  `libmcp_at_…` tokens first, then that verifier. Either way the user's id
  rides in the token's claims (with `auth: "oauth"` or `"api_token"`),
  and tools attribute their writes to it. OAuth access tokens are bound to
  the resource `https://<host>/mcp`; one minted for another resource is
  refused.
- **Consent:** only a signed-in browser session can approve or deny a
  client (`POST /api/oauth/requests/{id}/approve|deny` refuses bearer
  tokens with `403` and needs the CSRF header), so a static token cannot
  mint OAuth access for itself.
- **Size caps:** ingest respects `LIBRARY_MAX_UPLOAD_BYTES` (default
  100 MB, before base64 expansion); file retrieval is capped at 10 MB to
  keep tool results inside LLM context budgets.
- **Database connections:** tools and the token check take sessions
  from the process-wide pool the REST API uses (5 connections plus 10
  overflow). A client fanning out many parallel calls queues on that
  pool instead of exhausting Postgres connections.

## 1.5 OAuth endpoints

All advertised with the public origin from `LIBRARY_PUBLIC_BASE_URL`. The
resource and the issuer are both `https://<host>/mcp`, with no trailing
slash.

| Path | What it is |
|------|------------|
| `/.well-known/oauth-protected-resource/mcp` | RFC 9728 protected-resource metadata (the 401 points here); also at `/.well-known/oauth-protected-resource` |
| `/.well-known/oauth-authorization-server/mcp` | RFC 8414 authorization-server metadata; the same document at `/.well-known/oauth-authorization-server`, `/.well-known/openid-configuration[/mcp]` and `/mcp/.well-known/openid-configuration`, because clients differ in which they try. Library is not an OpenID provider; the OIDC spellings serve the OAuth document. |
| `/mcp/register` | Dynamic client registration (RFC 7591). Redirect URIs must match `LIBRARY_OAUTH_REDIRECT_ALLOWLIST` (default: the claude.ai, claude.com and ChatGPT callbacks, and `http://localhost/callback` / `http://127.0.0.1/callback` on any port). Public clients (`token_endpoint_auth_method: none`) are accepted. |
| `/mcp/authorize` | Starts the authorization-code flow (PKCE `S256` required) and sends the browser to the consent page, `/oauth/consent?request=…` |
| `/mcp/token` | Code and refresh-token exchange |
| `/mcp/revoke` | Token revocation (RFC 7009) |

The metadata advertises `authorization_response_iss_parameter_supported:
true`: the redirect back to the client carries `iss` (RFC 9207) next to
`code` and `state`. The only scope is `library` (full access as the
approving user); a client that asks for no scope gets the same access.

## 1.6 Behind Cloudflare Access or another auth proxy

The vendors' servers and the user's browser must reach the machine-facing
paths without a proxy login. Bypass exactly these and keep everything else
— including `/oauth/consent` and `/api/*` — behind the proxy, so signing
in to approve a client still passes through it:

- `/mcp` and `/mcp/*`
- `/.well-known/oauth-protected-resource` and `/.well-known/oauth-protected-resource/*`
- `/.well-known/oauth-authorization-server` and `/.well-known/oauth-authorization-server/*`
- `/.well-known/openid-configuration` and `/.well-known/openid-configuration/*`

Library's own auth guards all of them: every `/mcp` tool call needs a
token, and registration only accepts allow-listed redirect URIs.
