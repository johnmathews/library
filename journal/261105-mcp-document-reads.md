# MCP document reads and search parity

**Date:** 2026-11-05
**Branch:** `eng-mcp-reads`

## 1. Context

The MCP `get_document` returned a document's raw `ocr_text`, while full-text
search indexes its page markdown (`coalesce(pages_markdown, ocr_text)`). For a
thin-OCR image PDF the model found a document on words that `get_document`
then never showed it, and reported the content as absent. The MCP tools also
had their own copies of the list and detail serializers, which had drifted: no
comments, no `review_status`, no `amount_kind`, and no way to page past 50
search results.

## 2. What changed

- **One read service.** `src/library/document_reads.py` holds `load_pages`,
  `load_comments`, `document_text`, `list_item_fields` and `review_findings`.
  The REST list, detail, markdown and comments routes, Ask's `get_document`,
  the embed stage and both MCP read tools call it, and the second copies are
  gone: `select(DocumentPage)` and `select(DocumentComment)` now appear only
  there in `src/`.
- **`get_document`** returns the page markdown (OCR text only when a document
  has no pages), paged with `start_page`/`max_chars`, with `next_start_page`
  and a `continuation` sentence saying how to read on. It adds the comments
  (newest first, with the author's user id), `review_status` and findings,
  `amount_kind` and a `web_url` to the document's page in the web app.
- **`search_documents`** reaches the web app's list filters: `offset`, list
  `tags` (all), `projects` and `matters` (any), `facets`, `status`,
  `review_status`, `source`, `sort`/`direction`. Results are the web app's list
  rows. The single `tag`/`project`/`matter` parameters are gone: the only
  callers are LLM clients, which read the schema every session.
- Both read tools return typed models, so `tools/list` publishes an
  `outputSchema` for each.
- `DocumentListItem` gains `amount_kind` (REST, additive). Descriptions and
  the server instructions say not to add `amount_total` up across documents.
- `DocumentUpdate.tags` items are capped at 64 characters, the slug column's
  width. Before, a longer tag reached Postgres and raised a database error
  (a 500 over PATCH, masked over MCP).

## 3. Evidence

- The new behavioural tests written before the change were all seen failing
  on the old code. Where that failure was only "unknown argument", and for the
  two tests added afterwards (window edges, the MCP long tag), a hand-made
  mutant of the new code (no offset, a dropped filter, no resume at a cut
  page, comments oldest first, no `amount_kind`, an unbounded tag) turned the
  test red: 19 mutants, none survived.
- `tests/test_ask_get_document.py` and the detail, markdown and list tests in
  `tests/test_documents_api.py` pass unchanged. `mcp_server.py` and
  `document_reads.py` are at 100% line coverage.

## 4. Things learned

- Keeping Ask's text identical constrained the window: Ask fills up to its
  cap, cutting a page part-way. So the MCP window does the same, and its
  continuation resumes at the cut page, which is then read whole; only a page
  longer than `max_chars` needs a larger `max_chars` to finish.
- FastMCP reports a Pydantic validation error raised inside a tool as its own
  text, not masked. So the long-tag MCP test also checks that `tools/list`
  advertises `maxLength: 64`, which is what the parameter type adds.

## 5. What is deliberately not done

- No tool lists the facet vocabulary yet, so `facets` is usable only with
  keys the model already knows; W9's `get_library_context` renders it.
- Sender and recipient names have the same gap the tag had: `String(255)`
  columns, no length check. A longer name is still a database error.
- OCR-only documents page by `max_chars` alone (no character offset).
