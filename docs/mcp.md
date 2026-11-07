# MCP server

**Status:** active. **Last updated:** 2026-11-07 (§1.2: the SDK hold is about value, not the 4 MiB cap, because model output limits MCP uploads first; mcp also stays on 1.28.1). Earlier entries are in this file's history.
**Last verified:** 2026-11-07 — method: partial. §1.2's hold: the live archive has no document with source `mcp`; the output-length limit is reasoning, not measured. The mcp 1.30 point: the guard test on 1.28.1 (pass) and 1.30.0 (`413`) (journal 261107-dependabot-alerts). The rest carries forward journal 261107-library-followups-run-closed.
**Covers:** src/library/mcp_server.py, src/library/auth/service.py, src/library/auth/ratelimit.py, src/library/auth/oauth_provider.py, src/library/auth/oauth_service.py, src/library/api/oauth.py, src/library/config.py, src/library/db.py, src/library/app.py, src/library/mcp_rejected_log.py, frontend/src/views/OAuthConsentView.vue, frontend/src/views/SettingsView.vue, frontend/src/api/oauth.ts, src/library/documents_service.py, src/library/comments_service.py, src/library/notes_service.py, src/library/document_reads.py, src/library/search.py, src/library/ask/context.py, src/library/structured_query.py, src/library/filter_vocabulary.py

Library exposes a [Model Context Protocol](https://modelcontextprotocol.io)
server so LLM clients (Claude Code, claude.ai and Claude Desktop, ChatGPT,
the Anthropic API MCP connector, or any MCP-capable agent) can search the
archive by keyword or by meaning, read documents, total amounts, fetch
files, ingest new documents, write notes, correct document details and
add comments.

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

### What the clients do (seen 2026-11-05)

- **All three connect by URL alone** and every call is attributed to the
  user who approved them.
- **A client's first `POST /mcp` can get a `400`, and that is expected.**
  Clients that speak MCP 2026-07-28 open with a request carrying
  `MCP-Protocol-Version: 2026-07-28`. The installed SDK supports up to
  2025-11-25, so it answers `400` with JSON-RPC error `-32600`
  (`Unsupported protocol version`), and the client then falls back to
  `initialize`, as the spec's backward-compatibility section describes.
  The user sees nothing. Each refused request logs one line, such as
  `mcp_rejected status=400 method=tools/list protocol_version=2026-07-28
  reason="…"` (no params or arguments; 401 challenges are not logged), and
  a test pins the `400` so an SDK upgrade that changes it prompts a
  re-read of this paragraph.
- **Images:** claude.ai shows an `image` block from `get_document_file`
  to the user too, who can download it, even though the model may say
  it cannot show it. The tool text says so.
- **PDFs:** asked to "show" a PDF, claude.ai's model offered the text
  without calling `get_document_file`. The tool text now leads with
  "call it whenever the user asks to see, show, open, download or be
  sent a document, whatever the file type", and says to give the user
  `download_url` for a file that is not an image.
- **Chat attachments cannot be ingested from claude.ai.** The file never
  reaches the tool: the model would have to copy it out as base64 in
  the call (a 68 KB PDF is about 91,000 characters), and it rightly
  declines. The `ingest_document` text says to ask the user to upload in
  the web app or send it in by mail, and that the tool suits clients
  that read files from disk, such as Claude Code.
- **Edit confirmation is the model's call.** Library makes every applied
  edit match a fresh preview, but cannot tell whether a person saw it:
  an explicit "remove that tag" was applied straight after its preview.
  The tool text now says that a user's explicit request for exactly that
  change is their agreement, while a change the model chose itself, or a
  preview that differs from the request, needs asking first.
- **No client-side confirmation step, by decision (2026-11-07).** MCP has
  ways for a tool to ask the user a question: 2025 `elicitation/create`, and
  the 2026-07-28 revision's multi-round-trip tool calls, which work on a
  stateless transport. Neither is used. The 2025 form cannot reach any client
  over this stateless JSON-response transport. The 2026-07-28 form needs the
  SDK upgrade below. The household's main client also does not offer it:
  Anthropic's connector build guide lists no elicitation for claude.ai, while
  Claude Code and ChatGPT (Desktop and Work web) do. A server may not assume
  the client answers, so the preview and `preview_token` step would stay
  anyway. Re-check when the connector guide lists elicitation.
- **The SDK stays on fastmcp 3 / mcp 1 for now (held 2026-11-07).** fastmcp
  4 / mcp 2 would serve 2026-07-28 natively and end the `400` above. They
  also refuse MCP request bodies over 4 MiB (about 3 MiB per
  `ingest_document` file once base64 is decoded), fastmcp 4.1.6 has no
  setting to raise that, and a larger file gets a plain HTTP `413` the model
  cannot read.
  - **The cap matters less than it looks.** The model writes a tool call's
    arguments itself, so every character of `content_base64` is model
    output. A 3 MiB file is about 4 million characters, far more than a model
    writes in one reply, so the practical limit on an MCP upload is much
    lower than 3 MiB. That is reasoning, not a measurement. No document in
    the archive has come in through MCP (checked 2026-11-07).
  - **So the hold is about value, not the cap.** Today's gains are small: the
    `400` nobody sees, a confirmation step not wanted (above), and four mcp
    alerts that don't reach this server (next point). The cost is about one
    session of fixes, several on the OAuth path (journal
    `261107-library-followups-run-closed` §4).
  - **Upgrade when** mcp 1 stops getting security fixes, an alert lands that
    does reach this server, claude.ai offers mid-call questions, or a
    feature we want needs mcp 2. Then state the size limit in the
    `ingest_document` text, so the model doesn't send a file that would get
    the `413`.
- **mcp stays on 1.28.1, too.** mcp 1.30 backports the same 4 MiB cap, and
  fastmcp 3.4.11 builds its session manager without a size, so raising it
  would mean patching fastmcp and mcp internals. The four advisories 1.30
  fixes don't reach this server: it runs stateless (no sessions to pile up),
  its authorization server issues tokens only for itself, and two are
  client-side. mcp 1.30 also turns the SDK's `/authorize` endpoint into an
  ASGI app, which the binding-cookie wrapper in `auth/oauth_provider.py`
  would have to follow. `test_ingest_accepts_a_request_body_over_4_mib`
  (`tests/test_mcp.py`) fails with `413` under any SDK that caps the body.

## 1.3 Tools

All tools require auth.

**Errors.** Expected failures (unknown or deleted document, oversized upload
file, unsupported type) come back as MCP tool errors whose text is
written for the model and never points at the REST API, which an LLM
connector cannot call; where the user has to act, the text says to open
the document in the Library web app. Invalid arguments (a `document_id`
below 1 or above the Postgres `BIGINT` maximum, a `limit` above 50, a
`max_chars` outside 1 000–50 000, a tag longer than 64 characters, a
filter value that is blank once stripped) are argument-validation errors
naming the bound. Anything unexpected (a bug,
a database failure) is masked to `Error calling tool '<name>'`, so SQL
and schema details never reach a client; the full error goes to the
server log.

**Titles and annotations.** Every tool declares a human-readable `title`
and behaviour hints in `tools/list`, all with `openWorldHint: false`.
The read tools are `readOnlyHint: true` (clients such as ChatGPT ask the
user to confirm any tool without it). The four write tools are
`readOnlyHint: false`:

| Tool | `destructiveHint` | `idempotentHint` | Why |
|------|-------------------|------------------|-----|
| `ingest_document` | `false` | `true` | Only adds; the same bytes again return the existing document. |
| `update_document_metadata` | `true` | `true` | Overwrites stored values; the same change applied twice leaves the same state. |
| `add_comment` | `false` | `false` | Only adds; every call adds another comment. |
| `create_note` | `false` | `false` | Only adds; notes are never deduplicated, so every call files another note. |

| Tool | Parameters | Returns |
|------|------------|---------|
| `search_documents` | `query?` (websearch syntax, Dutch+English stemming), `kind?` (slug), `sender?` (case-insensitive substring of sender name), `recipient?` (case-insensitive substring of recipient name), `tags?` (slugs, a document must have all), `projects?` (slugs — see `list_projects`; any of them), `matters?` (slugs — see `list_matters`; any of them), `facets?` (`{facet_key: value_key}`, all of them), `language?` (`nld`/`eng`/`mixed`/`unknown`), `status?` (processing state), `review_status?` (`verified`/`needs_review`/`unreviewed`), `source?`, `date_from?`, `date_to?` (ISO dates, inclusive, on `document_date`), `sort` (`document_date` default, or `added_date`) and `direction` (`desc` default, or `asc`), both ignored with `query`; `limit` (default 10, max 50), `offset` (default 0) | `{results, total, offset, limit, next_offset}`. Each result is a row of the web app's list ([api.md §1.3.2](api.md)): `id, title, summary, kind, sender, recipient, tags, topics, projects, matters, document_date, due_date, expiry_date, language, status, mime_type, page_count, created_at, updated_at, has_searchable_pdf, has_thumbnail, review_status, review_findings, amount_total, currency, amount_kind, snippet, rank`. Ranked when `query` is given (snippets carry `<b>…</b>` match markers), otherwise ordered by `sort`. `next_offset` is the next page's `offset`, `null` on the last page. |
| `semantic_search` | `query` (natural language, any language; blank refused), `kind?`, `sender?`, `recipient?`, `tags?`, `projects?`, `matters?`, `facets?`, `date_from?`, `date_to?` (the same filters as `search_documents`), `limit` (default 10, max 20) | `{results, coverage}`. Each result is a `search_documents` row without `snippet`/`rank`, plus `passages` (the nearest passages, best first, shortened to fit the result; `[]` when only the keyword ranking found the document), `passages_truncated`, `page_number` (of the best passage, or `null`) and `score` (higher is better). `coverage` is `{matched, returned, unembedded}`. See *Searching by meaning* below. |
| `get_library_context` | — | `{context}`: the connected user's identity and the archive vocabulary as text. See *Searching by meaning* below. |
| `get_document` | `document_id`, `start_page` (default 1), `max_chars` (default 20 000, 1 000–50 000) | Every field of a search result except `snippet`/`rank` (including `review_status` and, when it is `needs_review`, `review_findings`), plus provenance (`source`, `original_filename`, `sha256`, `ocr_confidence`), `web_url`, the document's `text` with its paging fields (*Reading* below), `comments` `[{id, document_id, author_id, body, created_at}]` newest first, and the audit-trail `events` `[{event, created_at}]`. `topics` are read-only auto-extracted. |
| `money_query` | `aggregate` (`sum_amount` default, `distinct_senders`, `list`), `group_by?` (`sender`/`kind`, `sum_amount` only), `kind?`, `sender?`, `recipient?`, `tags?`, `projects?`, `matters?`, `facets?`, `date_from?`, `date_to?` (the `semantic_search` filters), `review_status?` | `{result_type, rows, coverage}`, `structured_query.query_documents`' result unchanged. `sum_amount` rows: `{key, total, currency, document_count, document_ids}` (`document_ids` cites at most 25; `document_count` is the true count) per currency (and per sender or kind with `group_by`; `key` is `null` without it), `total` a signed decimal string. `distinct_senders` rows: `{sender, document_count, document_ids}`; both at most 50 rows, the largest kept. `list` rows: `{id, title, sender, recipient, kind, document_date, amount_total, currency, review_status}`, up to 50. `coverage` is `{matched, included, excluded, needs_review}`. See *Money* below. |
| `get_document_file` | `document_id`, `variant` = `"original"` (default) or `"searchable_pdf"` | MCP content blocks plus structured content `{document_id, variant, filename, mime_type, size_bytes, content_included, download_url, note}`. Up to 2 MB the file itself is a second content block; over 2 MB there are no bytes, `content_included` is `false` and `note` says why. Not an error either way. See *Files* below. A variant that was never produced, or a file missing on disk, is an error. |
| `ingest_document` | `filename`, `content_base64` (PDF, JPEG, PNG, HEIC/HEIF, TIFF, plain text, Markdown, DOCX), `source_note?` | `{id, sha256, status, duplicate}`. Runs the same ingestion service as uploads (dedup by SHA-256, OCR + extraction queued); `source` is recorded as `mcp` and the calling token's user as uploader. A `source_note` is stored on the audit trail — but only when the ingest was not a duplicate, since a duplicate creates no new document to attach it to. Content matching a document in the trash is refused until it is restored or purged. |
| `list_kinds` | — | `{kinds: [{slug, name, document_count}]}` |
| `list_senders` | — | `{senders: [{id, name, document_count, colour}]}` — every sender, including those with no documents; `colour` is a chart colour or `null`. |
| `list_recipients` | — | `{recipients: [{id, name, document_count}]}` |
| `list_tags` | — | `{tags: [{slug, name, document_count}]}` |
| `list_projects` | — | `{projects: [{id, slug, name, description, document_count}]}` — archived projects are omitted. Use the slugs in the `projects` filter of `search_documents` and `semantic_search`. |
| `list_matters` | — | `{matters: [{id, slug, name, hint, document_count}]}` — archived matters are omitted. Use the slugs in the `matters` filter of `search_documents` and `semantic_search`. |
| `update_document_metadata` | `document_id`; any of `title?`, `summary?`, `document_date?`, `kind_slug?` (an existing kind), `sender?`, `recipient?` (names, matched case-insensitively, created if new), `tags?`, `projects?`, `matters?` (complete replacement lists; `[]` empties), `language?`, `amount_total?`, `currency?` (ISO 4217), `due_date?`, `expiry_date?`; `clear_fields?` (list of the nullable scalar fields above to empty); `apply` (default `false`); `preview_token?` | Preview (`apply` false): `{document_id, applied: false, changes: {field: {current, proposed}}, preview_token}`, nothing written. Apply: `{document_id, applied: true, updated_fields, current: {field: value}, review_status}`. See *Editing* below. |
| `add_comment` | `document_id`, `body` (stripped; blank refused) | `{id, document_id, author_id, body, created_at}` — the comment, authored by the token's user. Writes a `comment_added` audit event and queues a re-embed so the comment becomes searchable (the same service as the web app's *Add comment*). Unknown or deleted document: error. |
| `create_note` | `title` (stripped, 1–200 characters), `body_markdown` (Markdown; blank refused) | `{id, title, status, page_count, web_url, warning}` — the new note; `warning` is set when it was saved but its processing could not be queued. See *Notes* below. |
| `library_stats` | — | `{total_documents, by_status, by_kind, ingested_last_7_days, oldest_document_date, newest_document_date}`. `by_status` counts the lifecycle states that have documents (`received`, `ocr`, `extract`, `markdown`, `embed`, `indexed`, `failed`); soft-deleted documents are excluded from every figure. |

`search_documents` shares its query builder (`src/library/search.py`)
with `GET /api/documents`, so full-text matching, stemming, ranking and
every filter mean the same; see [api.md §1.3.3](api.md) for those
semantics. Two differences: sender and recipient are matched by name
substring (the web app filters by id), and a call returns at most 50
results (the web app's list allows 100).

**Filter values.** `search_documents`, `semantic_search` and `money_query`
share their filter parameter types. Every text value (`kind`, `sender`,
`recipient`, each tag, project and matter slug, each facet key and value)
is stripped of surrounding whitespace and refused when nothing is left:
a blank filter can only match nothing (or, for `sender`, everything), so
it would answer with a plausible wrong result. The rule is a schema
constraint (`minLength: 1`), so clients see it in `tools/list`. Ask's
tool loop has no schema validation and drops blank values instead
(`ask/engine.py`'s `_text_arg`). A slug or facet value the archive does
not have is refused only by `money_query` (*Money* below); the search
tools answer it with an empty result whose `total` or `coverage.matched`
is 0, which is a count, not a total that reads like an answer.

`search_documents`, `semantic_search`, `get_document`,
`get_library_context`, `money_query`, `create_note` and
`get_document_file` declare their result type, so `tools/list` carries an
`outputSchema` for each. `search_documents`, `semantic_search` and
`get_document` build their document fields
with `document_reads.list_item_fields`, the same function as the web
app's list and detail views (`src/library/document_reads.py`).

**Searching by meaning.** `semantic_search` is Ask's content search
([ask.md §1.2](ask.md), step 2): the query is embedded (bge-m3), and the
nearest passages (vector search) and the full-text ranking are fused with
reciprocal rank fusion, so a question in English finds a Dutch document
and a paraphrase finds the clause. Both tools call one service,
`search.hybrid_search`, which returns the hits and the `coverage` block.

- `coverage.matched` is how many documents passed the filters,
  `returned` how many came back, and `unembedded` how many matched
  documents have no passages indexed: those only a keyword match can
  reach. The description and the server instructions tell the model to
  read it before saying something is not in the archive.
- `limit` is fixed at 1–20 (the Ask tool's `top_k` ceiling is the
  setting `LIBRARY_ASK_SEARCH_MAX_TOP_K`); each result carries up to
  `LIBRARY_RETRIEVE_CHUNKS_PER_DOC` passages. The passages of one call
  share a budget of 24 000 characters (`SEMANTIC_PASSAGE_BUDGET`), split
  evenly across the hits, because MCP clients cap a tool result (Claude
  Code's default is 25 000 tokens). Whole passages are kept while they
  fit, the next is shortened, and `passages_truncated` says so. An empty
  `passages` list means only the keyword ranking found the document; it
  does not by itself mean the document is unindexed. There is no `offset`: a
  fused ranking has no stable pages, so `search_documents` is the tool
  for walking a whole result set.
- It takes Ask's filters, named as in `search_documents`, and no
  `status`, `review_status`, `language` or `source`: the coverage block
  reports reach, not what a filter removed (ask.md §1.2).
- When the embedding service does not answer, the call is a tool error
  telling the model to use `search_documents` instead.

**Library context.** `get_library_context` returns the block Ask appends
to its system prompt (`ask/context.py`), for the connected user: their
name, the recipient names linked to them, their own *About you* notes
(Settings → Ask), and the kind, matter, project and tag slugs, the
facets with their values, and the most frequent senders. Each household
member gets their own identity and notes; the vocabulary is the same.
It returns the rendered text only: the model reads text, the block is
already written and tested for a model, and the structured lists with
ids and counts are what the `list_*` tools return. The server
instructions tell the model to call it first.

**Reading: page text, paged.** `get_document`'s `text` is the document's
per-page markdown joined with blank lines, which is the text full-text
search indexes (`coalesce(pages_markdown, ocr_text)`), so a document
found on a word is shown with that word. A document with no pages yet
(not converted, or markdown off) falls back to its OCR text
(`text_source` is `pages`, `ocr_text` or `none`). Ask's `get_document`
reads through the same `document_reads.document_text`.

- The text runs from `start_page` for at most `max_chars` characters and
  always holds as much as fits: a page that does not fit whole is cut, not
  dropped. `text_start_page`/`text_end_page` are the first and last page
  in it, `text_page_count` the pages the document has, `text_total_chars`
  the whole text's length.
- When more follows (`text_truncated: true`), `next_start_page` is where
  to continue and `continuation` says so in words. Only whitespace after
  the window (a blank last page, trailing spaces) is not more text. If the window holds
  only the start of a page, that page is next and is read whole. If the
  cut page is the window's first (it is longer than `max_chars`),
  `next_start_page` is the following page and `continuation` says to ask
  again with a larger `max_chars` (up to 50 000) to read the cut page
  whole, or to use `get_document_file`.
- A single page longer than 50 000 characters cannot be read whole
  through `get_document`: single-page HTML emails and notes are stored
  as one page, and for those only `get_document_file` has the rest.
- OCR text has no pages: `start_page` above 1 is an error, and the only
  ways to more text are a larger `max_chars` or `get_document_file`. A
  `start_page` past the last page is an error naming the page count.
- `web_url` is `{LIBRARY_PUBLIC_BASE_URL}/documents/{id}`, the link push
  notifications carry; `null` when that setting is unset.
- Money: `amount_total` is always a magnitude and `amount_kind` says what
  it is (a `refund` is money back). The tool descriptions and the server
  instructions tell the model not to add amounts up across documents
  itself: the sign depends on `amount_kind`, and one payment often has
  several documents (an invoice and its receipt). They point it at
  `money_query` for totals (*Money* below).

**Money: `money_query`.** The only correct way to total amounts across
documents. It calls `structured_query.query_documents`, the service
behind Ask's `query_documents` tool ([ask.md §1.2](ask.md)), and returns
its result unchanged.

- `sum_amount` (the default) totals the `spend_facts` view
  ([money-facts.md](money-facts.md)): each amount is signed by its
  `amount_kind` (a refund subtracts), only real expenditure counts (a
  cover limit, a balance, an estimate or an undecided kind is left out),
  each payment counts once however many documents show it, and quotes
  are left out unless `kind` is `quote`. Totals are per currency;
  `group_by` splits them by sender or kind. `distinct_senders` and `list`
  return the same coverage block.
- Which document carries a payment is decided among the documents that
  pass every filter **except the date window** (the `spend_facts`
  ordering: a document with spend lines, then a `payment_made`, then the
  lowest id). The window then applies to that document. So
  `kind="utility-bill"` counts a bill whose receipt the archive-wide view
  would have counted, and a bill dated 28 September whose receipt is dated
  3 October counts in October only: a September total reports the bill as
  `payment_dated_elsewhere`, and adjacent windows add up. Totals under
  different non-date filters (`kind="utility-bill"` and `kind="receipt"`)
  can count the same payment and must not be added; `COVERAGE_GUIDE` tells
  the model so. `duplicate_payment` only names a document whose payment
  is in the rows ([money-facts.md](money-facts.md) §5.2).
- `distinct_senders` and `sum_amount` return at most 50 rows
  (`structured_query.MAX_GROUP_ROWS`): the senders with the most
  documents, the groups with the largest totals either side of zero. The
  documents of the rows cut are counted as `over_limit`, so a grouped
  result never looks complete when it is not, and a twin whose payment
  lands in a cut row is `over_limit` too; `sum_amount` without
  `group_by` has one row per currency.
- `coverage` says what the rows leave out. `matched` documents met the
  filters, `included` are the ones the rows account for, and `excluded`
  maps a reason to how many were dropped for it, so `included` plus the
  excluded counts is `matched`. The reasons: `no_amount`,
  `quote_not_spend`, `not_summable_kind`, `duplicate_payment`,
  `payment_dated_elsewhere` (the twin carrying the payment is dated
  outside the window, or undated),
  `no_sender`/`no_kind` when grouping (a document lacking the key whose
  payment no twin with the key carries), `filtered_review_status` when
  `review_status` filters, and `over_limit` for the rows a cap cut. `needs_review`
  counts included documents whose details validation flagged. The tool
  description and the server instructions tell the model to pass on
  any exclusion and any flag.
- The sentences that explain sign, dedup and coverage to the model are
  `structured_query.SUM_AMOUNT_GUIDE` and `COVERAGE_GUIDE`, quoted by
  this tool and by Ask's.
- Tool errors the model can act on: `group_by` with an aggregate other
  than `sum_amount`, `date_from` after `date_to`, an unknown `kind` (a
  concept word Ask's concept-to-kind map knows, `energy`, gets the slug
  to use instead, `utility-bill`), and unknown tag, project or matter
  slugs, facets or facet values, all named in one error that points at
  `get_library_context` and the `list_*` tools. One check serves this tool
  and Ask's `query_documents` (`filter_vocabulary.unknown_filter_values`);
  the search tools do not run it and return an empty result. An `aggregate` or
  `group_by` outside its enum is an argument-validation error.
- The archive is shared, so every household member gets the same total.

**Editing: preview, then apply.** `update_document_metadata` never
writes on the first call. Without `apply` it returns the current value
of every field it would change and the value applying would store, plus
a `preview_token`;
the tool description tells the model to show the user that preview and
call again with `apply: true`, the same arguments and the token. It asks
first whenever the model chose the change itself, or the preview differs
from what the user asked; when the user asked for exactly this change,
that request is the agreement. The fields and validators are the PATCH route's
(`DocumentUpdate`); an omitted parameter leaves its field unchanged, and
since `null` also means "unchanged" here, a field is emptied by naming
it in `clear_fields` (lists are emptied with `[]`; `language` cannot be
emptied). Naming a field that is not editable (for example `topics`) is
an argument error.

- *What a preview shows and checks.* The preview
  (`documents_service.preview_update`, shared with Ask) runs the edit
  inside a savepoint it rolls back, reads each edited field back from
  the database first, and shows that as `proposed`: the stored value,
  not the input. An amount is rounded to cents (`12.345` shows as
  `12.35`), a sender's whitespace is collapsed, a recipient given as a
  household member's username shows as her linked recipient, a project
  or matter name shows as the slug it resolves to. The spend-line sum
  triggers are forced to fire immediately, so anything the apply would
  refuse is refused at preview with the same message: an unknown kind
  slug, an `amount_total` that is split across spend lines (*clear or
  replace its spend lines before changing it*). Nothing persists: no new
  sender, tag or audit event.
- *The token.* A SHA-256 over the document id and, for each field being
  set, its current value and the stored value the preview showed, so it
  approves exactly what the user saw. It is stateless (the server keeps
  no record of previews) and is not a secret: it enforces "preview
  first" and detects change, not authority, which the bearer token
  already carries. Apply takes a row lock, reruns the preview's dry run
  to recompute it, and refuses a missing token (*preview first*) or a mismatch (*the
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

**Notes: `create_note`.** The web app's *New note*: both call
`notes_service.create_note`, so a note made over MCP is the same
born-digital `text/markdown` document (`source: note`, title locked
against re-extraction) with the token's user as uploader. The body is
written synchronously as the note's one page and its `pages_markdown`
search mirror, so `get_document` reads it and `search_documents` finds
it by its words as soon as the call returns; `process_document` is
queued for the kind, summary, tags and embeddings. Notes skip content
dedup (a salted SHA-256), so the same call twice makes two notes. That is
why the tool is not idempotent, and why its description and the server
instructions tell the model to call it once per note and not to retry
blindly. For the same reason, a job-queue failure after the note is
committed is not an error: the tool returns the saved note with a
`warning` telling the model to report it and not to create it again
(`notes_service.NoteNotQueued`). Editing a note stays in the web app ([ingestion.md *Notes*](ingestion.md)).

**Files: `get_document_file`.** The result is MCP content, not a base64
string in JSON:

- the first block is `text`: the metadata as JSON, which the structured
  content repeats (`outputSchema` describes it);
- the second, when the file is 2 MB or less, carries the file. JPEG,
  PNG, GIF and WebP are an `image` block the model can look at. A PDF
  and anything else is an embedded `resource` with a base64 `blob`;
  `text/*` (plain text, Markdown) is a `resource` with `text` when it
  is UTF-8, and a `blob` of the exact bytes otherwise. TIFF and
  HEIC are resources too, since LLM clients do not render them as
  images. The resource URI is
  `library://documents/{id}/{variant}/{filename}`, the filename
  percent-encoded as one segment (a `/` in a title becomes `%2F`): it names the file
  and is not readable through MCP `resources/read`.

The bytes appear once: before this, FastMCP sent a dict result twice (as
text and as structured content), so a 10 MB file was about 28 million
characters of base64 in one result. Over 2 MB only the metadata comes back, with
`download_url` = `{LIBRARY_PUBLIC_BASE_URL}/api/documents/{id}/original`
(or `/searchable.pdf`), the web app's own download link, `null` when the
setting is unset. That route is behind the web app's sign-in, so the
link works in the user's browser and not with an MCP token. The
description tells the model to give it to the user and not fetch it:
always for a file that is not an image, since the client may not show
the returned content, and for a file over 2 MB.
`note` points the user at the web app when there is no link.

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
  100 MB, before base64 expansion), though in practice how much base64 the
  model can write is the lower limit (§1.2); `get_document_file` returns bytes
  only up to 2 MB (about 2.7 MB of base64), to keep a tool result inside
  LLM context budgets, and above that a sign-in-protected download link.
- **Database connections:** tools and the token check take sessions
  from the process-wide pool the REST API uses (5 connections plus 10
  overflow). A client fanning out many parallel calls queues on that
  pool instead of exhausting Postgres connections.
- **Monitoring:** every tool call is counted and timed by tool and
  outcome (`library_mcp_calls_total`, `library_mcp_duration_seconds`) and
  logged as one `mcp_call` line with the caller's user id, never with
  arguments or results ([observability.md §3.1](observability.md)). A
  request the transport refuses with a 4xx other than 401 logs one
  `mcp_rejected` line (status, JSON-RPC method, protocol version, the
  SDK's error message; never params), see §1.2. CI's
  `compose-smoke` job calls `library_stats` on the built image the way
  the reverse proxy forwards it ([deployment.md §1.4](deployment.md)).

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

The vendors' servers must reach the machine-facing paths without a
proxy login. Bypass these four and keep everything else — including
`/oauth/consent` and `/api/*` — behind the proxy, so signing in to
approve a client still passes through it:

- `/mcp` and `/mcp/*`
- `/.well-known` and `/.well-known/*`

Library serves only the OAuth discovery documents under `/.well-known`
(anything else there is a JSON `404`), so the folder exposes nothing
more than naming each document would. Library's own auth guards the
rest: every `/mcp` tool call needs a token, and registration only
accepts allow-listed redirect URIs.

**Cloudflare Access.** Add a second self-hosted application on the
public hostname with those four paths as its destinations and one
policy whose action is **Bypass** for Everyone. An *Allow* policy for
Everyone still sends the request to the Access login. Access applies
the more specific application, so a wildcard application on the rest
of the zone stays as it is.

**Cloudflare Browser Integrity Check** can challenge claude.ai's
servers. A custom WAF rule with the action *Skip → Browser Integrity
Check* for Anthropic's outbound ranges on those paths prevents it:

```text
(http.host eq "library.example.org") and (ip.src in {160.79.104.0/21 153.61.208.0/20}) and (starts_with(http.request.uri.path, "/mcp") or starts_with(http.request.uri.path, "/.well-known/"))
```

ChatGPT connected without a rule of its own.

**Check from outside**, with no Access cookie:

```console
curl -s https://library.example.org/.well-known/oauth-protected-resource/mcp   # JSON, public https URLs
curl -si -X POST https://library.example.org/mcp | grep -i www-authenticate      # 401 + resource_metadata
curl -s -o /dev/null -w '%{http_code}\n' https://library.example.org/api/auth/login  # 302 to the proxy login
```
