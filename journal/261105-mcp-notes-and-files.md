# MCP notes and file results

> Purpose
>
> Why the MCP server gained `create_note` and why `get_document_file` now
> returns content blocks with a 2 MB inline cap (work unit W11, finding
> F14), and what is left for live client verification.

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
