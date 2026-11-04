# MCP edit and comment tools

**Date:** 2026-11-04
**Branch:** `eng-mcp-tools`

## 1. Context

Editing metadata was one of the four jobs the household wants an assistant to
do, and the MCP server had no write tool beyond ingest. The edit sequence
(apply, revalidate, the allocation guard, re-embed) already existed twice, in
the PATCH route and in Ask's confirmed edit; an MCP tool would have been a
third copy.

## 2. What changed

- **One edit service.** `documents_service.edit_document` is the only copy.
  It raises `DocumentEditError` (no FastAPI types) or `AllocationError`. PATCH
  keeps its exact statuses and messages. The `user_edited` audit event now
  records `user_id`, so with two people editing the trail says who.
- **`update_document_metadata`** (MCP): preview by default, apply with the
  preview's token. The preview dry-runs the real edit inside a savepoint with
  the spend-line triggers forced to fire, reads back what the database stored,
  and rolls back. So a refused edit fails at preview with apply's message, and
  the preview shows the stored value: `12.345` previews as `12.35`, a sender's
  extra whitespace is collapsed, a recipient name resolves to the linked person.
  The token hashes those stored values; apply locks the row, re-runs the dry
  run and refuses a missing preview, different fields, or a concurrent change.
- **`add_comment`** (MCP) through a new `comments_service`, which the REST
  route uses too.
- Ask's preview uses the same dry run, so the two cannot drift.
- The consent page again lists "change document details and add comments".

## 3. Evidence

- Every new behavioural test was seen failing first; where the first
  implementation passed outright, deliberate mutants (no dry run, committing
  dry run, skipped token check, `user_id=None`, no re-embed) each turned a
  test red. `mcp_server.py` line coverage stays at 100%.
- An independent review found the preview echoed the typed input while the
  database stored a normalised value (confirmed by a probe: previewed
  `12.345`, stored `12.35`, token still matched). Fixed as above.

## 4. Things learned

- **A refused Ask edit used to save half of itself:** setting `sender` and
  `tags: null` together changed the sender before the tags check refused,
  and the turn's commit stored it with no audit event (confirmed by a test).
  All checks now run before the first change.
- Rolling back a savepoint also undoes `SET CONSTRAINTS ALL IMMEDIATE`, so the
  real edit is still checked at commit (confirmed with a throwaway test).

## 5. What is deliberately not done

- A tag longer than the 64-character slug column is still a database error
  (masked over MCP; probably a 500 over PATCH, unobserved). Planned for W8.
- Deletion is not exposed as an MCP tool.
