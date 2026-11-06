# Ask refuses filter values the archive does not have

> Purpose
>
> Records why Ask's `query_documents` could answer a misspelt filter with
> a confident 0, and the shared check that now stops it. This is work unit
> W6 of the MCP follow-ups run (finding F9).

**Date:** 2026-11-06
**Branch:** `eng-mcp-followups-ask`

## 1. The gap

The follow-up as handed over read "unknown tag/project/matter slugs give
a silent zero over Ask and MCP". Scoping showed that was half out of date:

- **MCP `money_query`** already refused unknown values for every filter
  (#206).
- **Ask's `query_documents`** checked none of them, not even `kind`. A
  concept word as the kind (`energy`), a misspelt project or an unknown
  facet value each returned `rows []`, `matched 0` and no error. The model
  then reported that as a total of nothing.

## 2. The change

- **One check, one module.** `library/filter_vocabulary.py`'s
  `unknown_filter_values` covers six kinds of value:
  - kind, with the `CONCEPT_TO_KIND` "did you mean" hint;
  - tags;
  - projects;
  - matters;
  - facet keys;
  - facet values.

  The MCP copy, `_unknown_filter_values` plus an inline kind check, is
  deleted rather than kept beside it. `money_query` calls the shared
  function and keeps its messages word for word.
- **Ask returns an error.** `_run_query_documents` returns `{"error": ...}`
  naming only the unknown values, and points the model at the archive
  context in its system prompt.
- **Search stays permissive.** Ask's and MCP's search tools still answer
  an unknown value with an empty result. That was a recorded choice
  (`261105-mcp-document-reads.md`), and their counts already say nothing
  matched.
- **MCP facet wording.** The shared facets field description now says
  that search matches document labels while `money_query` matches money
  rows (a whole document, or one line), following W4.

## 3. Proof

Five new Ask tests failed with the check disabled. One showed the silent
zero itself; four failed with `KeyError: 'error'`. A guard test keeps a
known filter returning its total. The existing MCP refusal test passes
unchanged. `mcp_server.py` and the new module are at 100% line coverage.
