# MCP hardening foundation

**Date:** 2026-11-04
**Branch:** `worktree-eng-mcp-server`

## 1. Context

An evaluation of the MCP server asked whether it is good enough for four clients
(Claude Code, claude.ai, ChatGPT, other agents), two family users and four jobs:
find and read, ask (including money), edit metadata, ingest. The answer was no.
The public HTTPS origin sits behind Cloudflare Access, which redirects every
request to `/mcp/` to a login page; the server has no OAuth; and the tool set
covers finding and reading only. The follow-up plan has 13 units. This entry
covers the first, the foundation every later unit builds on.

## 2. What changed

- **Shared DB pool.** Tools and the token verifier took a fresh NullPool engine
  per call, so every MCP request opened at least two new Postgres connections.
  150 concurrent `library_stats` calls returned 55 bare HTTP 500s
  (`TooManyConnectionsError`, confirmed). They now use `db.get_sessionmaker()`,
  the bounded pool REST uses (5 + 10 overflow, waiting up to 30 s), and
  `create_app`'s lifespan calls a new `db.dispose_engine()` on exit so each app
  run, and each test client's event loop, starts with a fresh pool.
- **Tool annotations and titles.** Every tool declares `readOnlyHint` (ingest:
  not read-only, not destructive, idempotent). ChatGPT asks the user to confirm
  any tool without `readOnlyHint`.
- **Errors.** `mask_error_details=True`: `ToolError` text still reaches the
  model, anything else becomes a generic message, so an out-of-range id no
  longer returns SQL. `document_id` is bounded to a Postgres BIGINT. No error or
  description points at the REST API any more, which a connector cannot call.
- **Routing.** `/.well-known/*` is a JSON 404 instead of the SPA's 200 HTML (the
  OAuth discovery paths will live there). `POST /mcp` without the trailing slash
  returned 405 behind the SPA catch-all; a small ASGI middleware now rewrites
  `/mcp` to `/mcp/` before routing. A redirect would not do, since MCP clients
  do not reliably resend a POST body.
- **fastmcp** `>=3.4.11,<4`, locked at 3.4.11 (certifi follows). `<4` keeps a
  routine `uv lock --upgrade` from crossing the major version.
- **Docs.** `docs/mcp.md` no longer claims search is identical to
  `GET /api/documents` (it shares the query builder, not the filters or result
  shape); `deployment.md` and `architecture.md` re-stamped.

## 3. Evidence

- Each new behavioural test was seen red on the old code, for example the
  parallel test `Counter({'200:isError=False': 95, '500': 55})` and the
  slashless test `307 == 200`. Pins (soft-deleted document, disabled user,
  upload limit, missing variant, `library_stats` exclusion) were each broken by
  hand once to see them fail.
- Full backend suite green; `mcp_server.py` line coverage 100%.

## 4. What is deliberately not done

- OAuth, the Access bypass, and the new tools (edit, money, semantic search,
  page text, notes): later units of the same plan.
- The shared pool means a large MCP fan-out could make REST requests queue for
  a while instead of failing. Accepted at family scale; not load-tested
  (suspected, not observed).
- `uv` rewrote `uv.lock` to lock revision 5. The Docker builder's uv parsed it
  (`uv export --frozen` printed `fastmcp==3.4.11`); CI installs the latest uv.
