# MCP tool guidance for hosted clients, and the first-request 400 explained

> Purpose
>
> Records two changes from the live connector check: tool text that tells
> hosted clients when to call the file and ingest tools and when to confirm
> an edit, and a log line that names the reason for a refused `/mcp`
> request. Work units W2 and W3 of the MCP follow-ups run.

**Date:** 2026-11-05
**Branch:** `eng-mcp-followups-mcp`

## 1. Tool text (W2)

The live check (`261105-mcp-live-connectors.md` §3–§4) caught claude.ai's
model in three mistakes the tool text did not prevent:

- **"Show me the PDF"** got the document's text, and `get_document_file`
  was never called.
- **A returned image** was described as something the model "can't show",
  although claude.ai displays image blocks to the user.
- **A chat attachment** could not be filed. The model worked this out
  alone, but nothing in the tool text said so.

The descriptions and `SERVER_INSTRUCTIONS` now say:

- **`get_document_file`:** call it for any request to see, show, open,
  download or be sent a document, whatever the type. A returned image
  reaches the user. For anything that is not an image, give the user
  `download_url`. The pointer to `get_document` moved to the end, so it no
  longer steers a "show me" request towards the text.
- **`ingest_document`:** it suits clients that read file bytes themselves,
  such as Claude Code. A hosted chat's attachment never reaches it, so the
  model should send the user to the web upload or to mail-in rather than
  transcribe base64. The mail address is deliberately not in the text.
- **`update_document_metadata`:** confirm when the model chose the change.
  An explicit request for exactly that change is the user's agreement. A
  preview that differs from the request needs a question first. This
  matches what the user accepted live.

`test_file_ingest_edit_descriptions_carry_client_guidance` pins anchor
phrases in all three and in the instructions. It failed on the old text
before the change.

## 2. The first `POST /mcp` 400 (W3)

The 400 that claude.ai and ChatGPT got on the first request of a new
session is the MCP spec's own version probe, not a fault.

- A client that speaks MCP 2026-07-28 tries a modern request first.
- The installed SDK (mcp 1.28.1, revisions up to 2025-11-25) answers 400
  "Unsupported protocol version" (JSON-RPC -32600).
- The client then falls back to `initialize`, as the spec's
  backward-compatibility section describes.

A local probe built on Library's exact transport reproduced the 400 for
that header. The same probe ruled out a stale `Mcp-Session-Id`, a missing
Accept header, the slashless URL and GET/DELETE. The production pattern
fits: each 400 was followed within about 2 s by `initialize` and
`notifications/initialized`. Uvicorn never logged a reason, so the cause
was supported but not observed.

`McpRejectedLog` (`src/library/mcp_rejected_log.py`) wraps the `/mcp`
mount:

- **What it logs:** one line per 4xx other than 401, in the form
  `mcp_rejected status= method= protocol_version= reason=`.
- **What it reads:** only the JSON-RPC `method`, never params, with capped
  buffers.
- **Pass-through:** request and response bytes pass through unchanged.
- **Scope:** the OAuth endpoints live in the same mount, so their 4xx get
  a line too, with the OAuth `error_description` as the reason.
- **Logging setup:** `main.py` turns the logger on at INFO, as it does for
  `mcp_call`.

A pin test records the fallback contract: a raw `tools/list` with
`MCP-Protocol-Version: 2026-07-28` gets 400 and -32600. An SDK upgrade
that changes that behaviour will red it, which is the cue to revisit
`docs/mcp.md` §1.2. Upgrading to mcp 2.x or fastmcp 4.x, which serve the
newer revision, stays out of scope.

## 3. Follow-ups

- After the deploy, `docker logs library-webserver | grep mcp_rejected`
  should show `protocol_version=2026-07-28` on the first-request 400s.
  That observation upgrades the cause from supported to verified. A
  different version or reason reopens it.
- MCP elicitation, a formal confirmation step for edits, is not available
  on this transport (JSON responses, stateless). Hosted clients do not
  support it yet either.
