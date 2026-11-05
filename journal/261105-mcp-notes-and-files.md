# MCP notes, file results, CI smoke test and tool metrics

> Purpose
>
> Why the MCP server gained `create_note` and why `get_document_file` now
> returns content blocks with a 2 MB inline cap (work unit W11, finding
> F14), and what is left for live client verification. Then (W12, findings
> F11 and F16) how CI came to make a real MCP call against the built image,
> and how tool calls became measurable.

**Date:** 2026-11-05
**Branch:** `eng-mcp-files`

## 1. Context

Finding F14: files moved over MCP only as base64 strings inside JSON. For
`get_document_file` that meant a 10 MB file reached the model as about 28
million characters, because FastMCP sends a dict result twice (as text and
as structured content). And a model that wanted to file something it wrote
itself (a summary of a call, an agreement) had to base64 a Markdown file
into `ingest_document`. Whether claude.ai or ChatGPT can pass a chat
attachment into a tool argument at all is still open; that is checked live
in W13.

## 2. What changed

- **One note-create path.** `src/library/notes_service.py` holds
  `create_note` and the body helpers (`apply_body`, `lock_title`, the salted
  SHA-256 and the atomic file write), moved out of `api/notes.py`. The second
  copy is gone: `POST /api/notes` calls the service, and the edit and restore
  routes call `apply_body`/`lock_title` from it. The page row, the
  `pages_markdown` search mirror, the `received` event, the uploader and the
  queued `process_document` are written in one place. `tests/test_notes_api.py`
  passes unchanged.
- **`create_note`** (MCP): title (stripped, 1–200 characters, the web
  editor's cap) and a non-blank Markdown body; the uploader is the
  connected user. It returns `{id, title, status, page_count, web_url}`. Its
  annotations say it adds but is not idempotent, because notes skip dedup.
  So the description and the server instructions tell the model to call it
  once per note and to search for the title before any retry.
- **`get_document_file`** returns a FastMCP `ToolResult`: a `text` block with
  the metadata as JSON, then the file itself as an `image` block (JPEG, PNG,
  GIF, WebP) or an embedded `resource` (a blob for PDFs and other files, text
  for `text/*`). The structured content repeats the metadata, and an explicit
  `output_schema` (`DocumentFile`) describes it. The bytes now travel once.
- **2 MB cap, no error above it.** Over 2 MB the result is the metadata with
  `content_included: false`, a `note`, and `download_url`: the web app's
  own download route on `LIBRARY_PUBLIC_BASE_URL` (`document_reads.document_file_url`),
  `null` when that is unset. The link is behind the web app's sign-in, so the
  description says to give it to the user and not fetch it.

## 3. Evidence

- Every new test in `tests/test_mcp_notes_files.py`, and the updated
  listing tests in `tests/test_mcp.py`, failed on the old code (unknown tool,
  or a dict with `content_base64` and no content blocks). Then 23 hand-made
  mutants all turned a test red. They include: uploader not the caller,
  `pages_markdown` not re-synced, no page row, an unsalted SHA, TIFF as an
  image, an off-by-one cap, the searchable variant's link pointing at the
  original, and the instructions not naming the tool. One mutant first
  survived because the instructions name `create_note` twice; removing both
  mentions turned the test red.
- Adversarial fixtures: the note is created by a second user (not the first
  user), a trashed document carries the same coined search word, one
  document row has no file on disk, and the cap is tested on both sides of
  exactly 2 MB.
- `mcp_server.py` and `document_reads.py` stay at 100% line coverage over
  `tests/test_mcp*.py`.

## 4. Things learned

- FastMCP 3.4 drops the derived output schema when a tool returns
  `ToolResult`. Passing `output_schema=` on the decorator restores it, and
  the Python MCP client then validates the structured content against it on
  every call. So the tests check the schema without extra asserts.
- An image block is only safe for types LLM APIs accept. A `image/tiff`
  block risks the client refusing the whole result, so TIFF and HEIC travel
  as resources.

## 5. What is deliberately not done

- No claim about what hosted clients (claude.ai, ChatGPT) do with an image or
  resource block, or whether they can attach a file to `ingest_document`.
  W13 verifies that live.
- The embedded resource URI (`library://documents/{id}/{variant}/{filename}`)
  is a name only; the server registers no MCP resources.
- No short-lived signed download link: `download_url` relies on the user's
  web-app session.
- `POST /api/notes` accepts any title length, but `original_filename` is
  `{title}.md` in a 1024-character column, so a longer title is a database
  error there (checked: a 1,100-character title raises asyncpg's
  `StringDataRightTruncationError`, so it is an unhandled 500). Not changed
  here, because REST behaviour is out of scope; the MCP tool caps titles at
  200.

## 6. W12: CI smoke test and tool metrics

### 6.1 Context

F11: no CI gate sent an MCP request to the built image. Every MCP test runs
in-process with `Host: testserver`, and the one regression of this kind
already seen (fastmcp 3.4.3's Host/Origin guard behind a proxy) only shows
under a real server and a real `Host`. F16: after launch nothing showed
which tools were used, slow or failing; uvicorn's `POST /mcp/ 200` was the
only trace.

### 6.2 What changed

- **`scripts/mcp_smoke.py`** (stdlib only) runs in `compose-smoke` with a
  public `Host` and `X-Forwarded-Proto: https`. It checks the no-token `401`
  and its `resource_metadata`, logs in, creates a token, runs `initialize`,
  `notifications/initialized` and `tools/call library_stats`, and requires
  `isError: false`. The job now writes a `.env` that turns MCP OAuth and
  metrics on, as production has them. Two more steps then grep the call's
  log line from `docker compose logs api` and its counter from `/metrics`.
- **`telemetry.record_mcp_call(tool, outcome, duration_s)`** adds
  `library.mcp.calls` and `library.mcp.duration` (seconds, with explicit
  buckets from 10 ms to 30 s). Prometheus renders them as
  `library_mcp_calls_total` and `library_mcp_duration_seconds`.
- **`ToolCallMetrics`**, an `on_call_tool` middleware, records the metric and
  logs `mcp_call tool=… user_id=… outcome=… duration_ms=…`, and re-raises
  everything untouched.
- **`main.py` gives the API process a log handler.** See 6.4.

### 6.3 Evidence

- Red proofs, each run locally against a `docker compose` build of this
  tree (no `act` installed): the MCP mount moved to `/mcp-broken` gave
  `mcp-smoke: FAIL no token: expected 401, got 405`; the strict Host guard
  switched on (the 3.4.3 failure) gave `got 421: Misdirected Request`; and
  without `main.py`'s handler the smoke passed but the log grep found nothing.
  The final tree went green on all three steps.
- Nine hand-made mutants each turned a metrics or log test red, among them
  the middleware not registered (the pre-W12 state), masked errors counted as
  `tool_error`, an unknown tool name kept as the label, arguments in the log
  line, and the SDK's default buckets. Three more turned the script's own
  tests red.
- `mcp_server.py` stays at 100% line coverage over `tests/test_mcp*.py`.

### 6.4 Things learned

- **An INFO log in the API process went nowhere.** uvicorn configures only
  its own loggers, so a `library` record below WARNING had no handler and was
  dropped. That held for every INFO line the API ever wrote, not only the new
  one. `main.py` now adds a root handler at WARNING (the worker's format) and
  turns on just the call logger at INFO. The compose-smoke log step proves it
  in the image, and the red run above shows why it is needed.
- **FastMCP masks inside `call_tool`**, so a middleware never sees the
  original exception, only the `ToolError("Error calling tool '…'")` it
  became. The middleware tells `error` from `tool_error` by that fixed text.
  `test_tool_call_metrics_recorded` pins the text against the installed
  FastMCP, so an upgrade that changes it goes red.
- **A client picks the tool name.** An unknown name raises FastMCP's
  `NotFoundError` before any tool runs, and is counted as `unknown`. Without
  that, every distinct string a client sent would become a new time series.

### 6.5 What is deliberately not done

- No token CLI. The plan assumed one (`library token create`), but none
  exists, so the smoke uses the documented login-then-`POST /api/auth/tokens`
  path.
- The user is not a metric label (cardinality); it is in the log line only.
- FastMCP's own logger still prints argument values for a call that fails
  validation, and every call's arguments at DEBUG. That is upstream; the
  observability doc says to leave `FASTMCP_LOG_LEVEL` at `INFO`.
- The red proof ran on a local arm64 build, not on the CI runner. The CI run
  of this branch is the first time the steps run on amd64 with the full
  stack, embedder included.

## 7. Review fixes

An independent review of W11 found four low-severity defects, fixed in the
PR that ships W11 and W12. Each new test was seen red on the previous code.

- **Filename in the resource URI.** `quote()` left `/` unescaped and
  `AnyUrl` collapsed dot segments, so a note titled "Call 4/3" got a URI
  ending `.../Call%204/3.md`, and `../../x` dropped the document id and
  variant. Now `quote(filename, safe="")`.
- **Queue failure after commit.** `create_note` committed the note and then
  raised a masked error when `process_document` could not be queued, which
  invites a retry and a duplicate note. `notes_service.NoteNotQueued` carries
  the saved document; the MCP tool returns it with a `warning`, and the REST
  route still lets it propagate.
- **Non-UTF-8 text.** A Latin-1 `text/plain` file came back as text with
  replacement characters. Text is now decoded strictly, with the exact bytes
  as a blob resource on failure.
- **Note title length.** W11's own find: `NoteCreate.title` had no maximum,
  so a title over about 1,000 characters overflowed the 1,024-character
  `original_filename` column and was a 500. Create and edit now cap it at
  200 characters, the web editor's limit, as one constant in `schemas.py`
  that the MCP tool imports.
