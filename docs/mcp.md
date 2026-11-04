# MCP server

**Status:** active. **Last updated:** 2026-11-04 (§1.3: `update_document_metadata` (preview, then apply with a preview token) and `add_comment`; §1.4: editing is exposed, deletion is not; §1.2: the consent page lists changing details and adding comments). Earlier entries are in this file's history.
**Last verified:** 2026-11-04 — method: partial, scoped to the two new §1.3 rows, the editing paragraph, §1.4's first bullet and §1.2's consent sentence. Read `update_document_metadata`, `_preview_token`, `_dry_run_edit`, `add_comment` and `comments_service.py` against the prose; the 15 tests in `tests/test_mcp_edit.py` green, 14 seen red with the tools absent, and mutants of the dry run, rollback, token check, user id, re-embed and DB-error re-raise each turned one or more red. The rest carries its earlier verifications forward.
**Covers:** src/library/mcp_server.py, src/library/auth/service.py, src/library/auth/ratelimit.py, src/library/auth/oauth_provider.py, src/library/auth/oauth_service.py, src/library/api/oauth.py, src/library/config.py, src/library/db.py, src/library/app.py, frontend/src/views/OAuthConsentView.vue, frontend/src/views/SettingsView.vue, frontend/src/api/oauth.ts, src/library/documents_service.py, src/library/comments_service.py

Library exposes a [Model Context Protocol](https://modelcontextprotocol.io)
server so LLM clients (Claude Code, claude.ai and Claude Desktop, ChatGPT,
the Anthropic API MCP connector, or any MCP-capable agent) can search the
archive, read documents, fetch files, ingest new documents, correct
document details and add comments.

- **Endpoint:** `https://<your-host>/mcp` (Streamable HTTP transport,
  stateless, JSON responses). `/mcp/` with a trailing slash is the same
  endpoint: `/mcp` is rewritten onto it before routing, for every method,
  so no client has to follow a redirect. It is part of the main API
  process — no extra container or port.
- **Auth:** every request needs `Authorization: Bearer …` with one of two
  kinds of token, and each acts as one Library user:
  - **OAuth** (claude.ai, ChatGPT, Claude Code): on when
    `LIBRARY_PUBLIC_BASE_URL` is set to the server's public `https://`
    origin, or to a plain `http://` origin on `localhost`/`127.0.0.1`
    (local testing only). The client discovers Library's authorization server, the user
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
client does is attributed to whoever approved it.

**What the user sees.** The client opens Library's sign-in page if the
browser is not signed in, then the consent page. It names the app (or,
when the app registered no name, the host it returns to), shows
prominently the host the answer goes back to (`claude.ai`, `chatgpt.com`,
`localhost` for Claude Code — the thing to check against the app you
started from), the account it connects as, with a *Not you?* link that
signs out and back in as someone else, and what the app can do: search
and read every document, download files, add documents, and change
document details and add comments — everything it adds or changes
recorded as that user. **Approve**
returns to the app with the authorization code; **Deny** returns with
`access_denied`. A request is valid for 10 minutes and works only in the
browser that started it: an expired one offers only *Return to the app*
(a deny, so the app stops waiting), and an unknown, used or forwarded
link says to start again from the app. The page works at phone width.

**Disconnecting.** Settings → **Connected apps** lists each approved app
with when it connected and was last used; *Disconnect* (confirmed inline)
revokes it and its tokens at once ([api.md §1.9.4](api.md)). When OAuth
is off the tab says so instead of listing.

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
and behaviour hints in `tools/list`, all with `openWorldHint: false`.
The read tools are `readOnlyHint: true` (clients such as ChatGPT ask the
user to confirm any tool without it). The three write tools are
`readOnlyHint: false`:

| Tool | `destructiveHint` | `idempotentHint` | Why |
|------|-------------------|------------------|-----|
| `ingest_document` | `false` | `true` | Only adds; the same bytes again return the existing document. |
| `update_document_metadata` | `true` | `true` | Overwrites stored values; the same change applied twice leaves the same state. |
| `add_comment` | `false` | `false` | Only adds; every call adds another comment. |

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
| `update_document_metadata` | `document_id`; any of `title?`, `summary?`, `document_date?`, `kind_slug?` (an existing kind), `sender?`, `recipient?` (names, matched case-insensitively, created if new), `tags?`, `projects?`, `matters?` (complete replacement lists; `[]` empties), `language?`, `amount_total?`, `currency?` (ISO 4217), `due_date?`, `expiry_date?`; `clear_fields?` (list of the nullable scalar fields above to empty); `apply` (default `false`); `preview_token?` | Preview (`apply` false): `{document_id, applied: false, changes: {field: {current, proposed}}, preview_token}`, nothing written. Apply: `{document_id, applied: true, updated_fields, current: {field: value}, review_status}`. See *Editing* below. |
| `add_comment` | `document_id`, `body` (stripped; blank refused) | `{id, document_id, author_id, body, created_at}` — the comment, authored by the token's user. Writes a `comment_added` audit event and queues a re-embed so the comment becomes searchable (the same service as the web app's *Add comment*). Unknown or deleted document: error. |
| `library_stats` | — | `{total_documents, by_status, by_kind, ingested_last_7_days, oldest_document_date, newest_document_date}`. `by_status` counts the lifecycle states that have documents (`received`, `ocr`, `extract`, `markdown`, `embed`, `indexed`, `failed`); soft-deleted documents are excluded from every figure. |

`search_documents` shares its query builder (`src/library/search.py`)
with `GET /api/documents`, so full-text matching, stemming and ranking are
the same; see [api.md §1.3.3](api.md) for those semantics. It exposes a
subset of the REST filters (one tag, project or matter per call; sender
and recipient by name substring; no facet, status or review filters, no
`offset`) and returns at most 50 results per call.

**Editing: preview, then apply.** `update_document_metadata` never
writes on the first call. Without `apply` it returns the current and
proposed value of every field it would change, plus a `preview_token`;
the tool description tells the model to show the user that preview and
call again with `apply: true`, the same arguments and the token only
after they agree. The fields and validators are the PATCH route's
(`DocumentUpdate`); an omitted parameter leaves its field unchanged, and
since `null` also means "unchanged" here, a field is emptied by naming
it in `clear_fields` (lists are emptied with `[]`; `language` cannot be
emptied). Naming a field that is not editable (for example `topics`) is
an argument error.

- *What a preview checks.* The preview runs the edit inside a
  transaction it rolls back, with the spend-line sum triggers forced to
  fire immediately, so anything the apply would refuse is refused at
  preview with the same message: an unknown kind slug, an
  `amount_total` that is split across spend lines (*clear or replace its
  spend lines before changing it*), so the user is not asked to approve
  a change the apply would refuse as things stand. Nothing persists: no
  new sender, tag or audit event.
- *The token.* A SHA-256 over the document id and, for each field being
  set, its current and proposed value. It is stateless (the server keeps
  no record of previews) and is not a secret: it enforces "preview
  first" and detects change, not authority, which the bearer token
  already carries. Apply recomputes it from the database under a row
  lock and refuses a missing token (*preview first*) or a mismatch (*the
  document changed since the preview, or the fields differ*): another
  value, another field, another document, or someone else having
  changed one of those fields in between. A change to a field the edit
  does not touch does not invalidate it.
- *Apply* runs the same edit service as the web app and Ask
  (`documents_service.edit_document`): the fields become user-edited (so
  re-extraction leaves them alone), validation is re-run, a header
  field (sender, date, kind, title) queues a re-embed, and the
  `user_edited` audit event records `edited_by: "mcp"` and the token's
  `user_id`. An amount allocated across spend lines after the preview
  is still refused at apply, with nothing written.

## 1.4 Security notes

- **Token = full account access.** MCP tools run as the token's user
  (for OAuth, the user who approved the client): the connected LLM can
  read and search everything, ingest documents, change any document's
  details and add comments. Edits are preview-first (§1.3): the first
  call writes nothing, and the apply needs a token from that preview of
  the same change, which also refuses an edit if the document changed in
  between (for example, another household member edited it). Every
  write is attributed to the user — the uploader on an ingest, the
  author on a comment, `edited_by: "mcp"` plus `user_id` on an edit.
  Document deletion is not exposed, nor are trash, restore and purge.
- **Transport:** run behind TLS (reverse proxy). Tokens travel in the
  `Authorization` header on every request.
- **Revocation:** both kinds of token validate against the database on
  every request (hashed at rest, last use touched at ~5-minute
  granularity), so revoking a static token, disconnecting an OAuth client
  (`DELETE /api/oauth/grants/{id}`, which kills its access and refresh
  tokens) or disabling a user (`library user disable`, or deactivating
  them in Admin → Users) cuts MCP access immediately. Disabling also
  revokes every OAuth grant and token of the user, so re-enabling them
  brings no connected app back: each has to be approved again.
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
- **Consent is bound to the browser that started it.** `/mcp/authorize`
  sets a cookie named `library_oauth_request_<16 hex>` (a prefix of the
  request id's SHA-256) holding a fresh random secret, whose hash is
  stored on the pending request: `HttpOnly`, `SameSite=Lax`, `Path=/`,
  `Secure` when the issuer is `https`, `Max-Age` 600 (the request's
  lifetime). Reading, approving and denying the request need that cookie;
  without it they answer `404`, like an unknown request, and say to start
  again from the app. So a consent link someone forwards — say, a
  household member who started connecting *their own* claude.ai — cannot
  be approved by whoever opens it, even signed in, and the attempt does
  not use the request up. One cookie per request, so two apps connecting
  in the same browser at once do not interfere; answering clears it.
- **Allow-list changes apply to existing clients.** A narrowed
  `LIBRARY_OAUTH_REDIRECT_ALLOWLIST` is enforced at `/mcp/authorize`, not
  only at registration, so clients registered under the old list lose
  the removed redirects at once.
- **Refresh tokens keep their scope.** A refresh that asks for fewer
  scopes narrows the new access token only (RFC 6749 §6).
- **Accepted limits.** Replaying an already-used authorization code is
  refused, but does not revoke the tokens that code produced (an RFC 6749
  SHOULD): codes are deleted on use, so there is nothing to trace them by.
  Refresh-token reuse detection is strict: two refreshes racing from one
  client count as reuse and revoke the whole family, so that client has
  to be approved again. The SDK's own error redirects from
  `/mcp/authorize` (for example `invalid_scope`, or a `resource` other
  than `https://<host>/mcp`) carry no `iss`, although the metadata
  advertises `iss` support; the approve and deny redirects do carry it.
- **Rate limits on the OAuth endpoints.** `/mcp/token` and `/mcp/register`
  are open to the internet (§1.6), so every `POST` to them counts per
  client: 60 per 15 minutes for the token endpoint (a refresh per
  connector is about hourly, so this leaves room for several connectors
  behind one shared egress address), 10 per hour for registration (once per
  connector setup). Over the limit: `429`, `Retry-After` in seconds and
  `{"error": "slow_down", …}` (RFC 6749 has no 429 code; clients treat it
  as a failed request). A refused request is not counted, so waiting out
  `Retry-After` always works. Counters are in-process and reset on restart.
  The client is the socket peer unless `LIBRARY_TRUSTED_CLIENT_IP_HEADER`
  names a header the proxy sets (`docs/deployment.md` §1.5.1); without it,
  everyone behind the proxy shares one bucket. `/api/auth/login` is
  throttled too (`docs/api.md` §1.9.1).
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
| `/.well-known/oauth-authorization-server/mcp` | RFC 8414 authorization-server metadata; the same document at `/.well-known/oauth-authorization-server`, `/mcp/.well-known/oauth-authorization-server`, `/.well-known/openid-configuration[/mcp]` and `/mcp/.well-known/openid-configuration`, because clients differ in which they try. Library is not an OpenID provider; the OIDC spellings serve the OAuth document. |
| `/mcp/register` | Dynamic client registration (RFC 7591). Redirect URIs must match `LIBRARY_OAUTH_REDIRECT_ALLOWLIST` (default: the claude.ai, claude.com and ChatGPT callbacks, and `http://localhost/callback` / `http://127.0.0.1/callback` on any port). Public clients (`token_endpoint_auth_method: none`) are accepted. |
| `/mcp/authorize` | Starts the authorization-code flow (PKCE `S256` required) and sends the browser to the consent page, `/oauth/consent?request=…`, setting that request's binding cookie (§1.4). The redirect URI must be registered *and* still on the current allow-list; one that is not gets a `400` directly, never a redirect. |
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
