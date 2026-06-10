# W13 — MCP server

**Date:** 2026-06-10. **Unit:** W13 (improvement plan §1.3.13).

## What landed

- `src/library/mcp_server.py` — FastMCP 3.4.x server ("Library") with
  eight tools: `search_documents`, `get_document`, `get_document_file`,
  `ingest_document`, `list_kinds`, `list_senders`, `list_tags`,
  `library_stats`. Tool descriptions written for LLM consumers (when to
  use, what comes back, cross-references between tools).
- `src/library/search.py` — the FTS query builder extracted from
  `api/documents.py`. `build_document_query(q, DocumentFilters)` returns
  a `(statement, count, has_rank)` triple; the REST endpoint and the MCP
  tool both call it, so search semantics cannot drift. REST behaviour is
  byte-identical (same conditions, ordering, headline options); the W7
  test suite passes unchanged. New for MCP: `sender_contains`
  (case-insensitive, LIKE-escaped substring on sender name — MCP callers
  know names, not ids).
- Mounting: `create_app()` builds the MCP ASGI app via
  `mcp.http_app(path="/", stateless_http=True, json_response=True)`,
  mounts it at `/mcp`, and runs its lifespan inside the FastAPI lifespan
  (`async with job_app.open_async(), mcp_http.lifespan(mcp_http)`),
  exactly as FastMCP's FastAPI integration prescribes. Stateless + JSON
  because the tools are pure request/response: no session affinity behind
  a proxy, no long-lived SSE streams, and the app stays testable through
  an in-process ASGI transport.
- Auth: `LibraryTokenVerifier(TokenVerifier)` — FastMCP's verifier hook —
  calls the same `validate_api_token` service as the REST API. FastMCP
  wires it into bearer middleware that 401s before tool code runs; the
  user id travels in the access-token claims and is recorded as uploader
  on `ingest_document`. Chosen over a hand-rolled ASGI middleware because
  the verifier is one method and buys the spec-compliant 401 +
  `WWW-Authenticate` handling and `get_access_token()` for free; the
  OAuth-shaped parts of the interface simply stay unused (no routes, no
  metadata).
- Docs: `docs/mcp.md` (connection guide for Claude Code / API connector /
  generic clients, token lifecycle, tool reference, security notes).

## Decisions and gotchas

- **DB sessions outside DI:** MCP tools and the verifier don't go through
  FastAPI dependencies, so they open a NullPool engine per call (same
  pattern as the CLI). Avoids loop-bound pooled connections; trivial cost
  at family scale.
- **OCR truncation at 20k chars, files capped at 10 MB (base64)** to keep
  tool results inside LLM context budgets; the error/note points at the
  REST endpoints for the full payloads.
- **Testing:** the official `mcp` client over Streamable HTTP through
  `httpx.ASGITransport` against the real app — auth middleware included —
  with the app lifespan entered manually. anyio cancel scopes cannot
  cross pytest-asyncio's fixture setup/teardown task boundary, so the
  lifespan + client stack is entered *inside* each test via factory
  fixtures (`running_app()` / `mcp_connect()`), not yielded from async
  fixtures.
- `source_note` on `ingest_document` is recorded as an `mcp_source_note`
  ingestion event (the audit trail was already the right place; no schema
  change).
- Mutation tools (edit/delete) deliberately not exposed over MCP in v1.
