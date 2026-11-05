# MCP document reads, semantic search and money totals

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

## 6. W9: semantic search and library context

Finding F5: the MCP server had keyword search only, while Ask's hybrid
retriever (vector + full-text, fused with RRF) needs nothing but the
embedding service.

- **One retrieval service.** `search.hybrid_search` runs `semantic_search`
  plus `search_reach` and returns the hits with a `SearchCoverage`
  (`matched`, `returned`, `unembedded`). Ask's `_run_semantic_search` now
  calls it, and its inline retrieval and coverage assembly are gone. Callers
  still embed the query themselves, so an embedding failure stays theirs to
  word (Ask: a tool-result error; MCP: a `ToolError` pointing at
  `search_documents`).
- **`semantic_search`** takes Ask's filters, named as in `search_documents`
  (`sender`, `recipient`, list `tags`/`projects`/`matters`, `facets`,
  dates), plus `limit` up to 50. Results are `search_documents` rows plus
  `passages`, `page_number` and `score`, and the coverage block. The nine
  shared filters are now one `Annotated` type each, so the two tools
  describe a filter identically.
- **`get_library_context`** returns Ask's rendered archive-context block for
  the caller: identity line, own recipient names, *About you* notes, the
  vocabulary. Text only: the block is already written for a model, and the
  structured lists are the `list_*` tools. `search_documents`' `facets`
  description and the server instructions point at it; the instructions say
  to call it first and when to use which search.
- The `list_tags`/`list_projects`/`list_matters` descriptions still named
  the singular filters W8 removed; they now name `tags`/`projects`/`matters`.

**Evidence.** The new tests in `tests/test_mcp_semantic.py` and the extended
listing and outputSchema checks in `tests/test_mcp.py` were all red before
the tools existed. Since several of those reds were only "unknown tool", 21
hand-made mutants were run (each filter dropped from the mapping, one
passage only, no page, `limit` ignored, the embedding error uncaught, the
query not stripped, the context of a fixed user, the missing-user check
removed, the `facets` pointer and the instructions line removed, coverage
without `unembedded`, `returned` set to `matched`, Ask's coverage
truncated): all 21 turned their test red. `mcp_server.py` stays at 100%
line coverage over the MCP test files.

**Things learned.** The plan said existing test patches would hold because
callers keep calling `embed_query`. That held for `embed_query`, but four
`tests/test_api_ask.py` tests also replaced `semantic_search` and
`search_reach` on the engine module, names that moved with the core. They
now patch them on `library.search`, where `hybrid_search` looks them up;
the fakes and every assertion are unchanged. `tests/test_ask_search_filters.py`
passes unchanged.

**Not done.** A keyword-only hit has no passage; a `ts_headline` snippet
for it would be one more query per call. `semantic_search` has no
`offset`, by design (a fused ranking has no stable pages).

## 7. W10: money totals, and three W8 review fixes

Finding F4: the only MCP route to "how much did we spend on X" was the model
adding up `amount_total` itself, which adds refunds, counts cover limits and
counts an invoice and its receipt twice.

- **`money_query`** calls `structured_query.query_documents`, the service behind
  Ask's `query_documents`: `aggregate` (`sum_amount` default, `distinct_senders`,
  `list`), `group_by` (sender or kind), the `semantic_search` filters plus
  `review_status`. The result is that function's, field for field, typed with its
  own `AmountGroup`/`SenderGroup`/`DocumentRef` rows and `Coverage`, so the
  `outputSchema` is the service's shape and there is no second copy of it.
- **One wording.** The sign/dedup sentence and the coverage paragraph of Ask's
  tool text are now `SUM_AMOUNT_GUIDE` and `COVERAGE_GUIDE` in
  `structured_query.py`. Ask's tool text quotes them, and so does the MCP
  description. Ask's `TOOLS`, dumped as JSON before and after, is byte-identical.
- `get_document`, `search_documents` and the server instructions point at
  `money_query` for totals.
- **Errors the model can act on** are `ToolError`s: `group_by` without
  `sum_amount` (the service would ignore it), `date_from` after `date_to`, and an
  unknown `kind`, with the slug to use when it is a concept word from
  `CONCEPT_TO_KIND` (`energy` → `utility-bill`). The last two would otherwise give
  a total of 0 that reads like an answer. A bad `aggregate` or `group_by` is
  stopped by the enum in the schema.
- W8 review fixes: the `search_documents` description said every filter
  AND-combines, but `projects` and `matters` match any of their values (now said,
  with a `projects` union test). A blank tail after the text window (an empty
  last page, trailing spaces) no longer counts as more text. `docs/mcp.md` says a
  page over 50 000 characters cannot be read whole through `get_document`.

**Evidence.** The fixture is adversarial: a refund from the same sender quoting
the invoice's reference, an invoice and a receipt for one payment on the same
day, a cover limit, a trashed payment, a payment on 1 March just past a February
window that ends on the leap day, and a payment uploaded by a second household
member and flagged `needs_review`. The hand-computed net is 91.85; summing
`amount_total` gives 5222.35, an added refund 122.35, a double-counted payment
191.85, a dropped second member 84.75, so each mistake shows as its own number.
The tool returned 91.85, and the same answer to the second member. The money
tests were red before the tool existed (only "unknown tool"), so 20 hand-made
mutants were run (aggregate or `group_by` not passed on, each of the ten filters
dropped, each of the four error checks removed, the coverage emptied, the shared
wording and the `money_query` pointers removed): all 20 turned a test red. The
blank-tail test was red on the old code for both the page and the OCR branch. The
new window test (cut exactly after a page separator) passed on the old code; the
`start < len(text)` → `<=` mutant turns it red, and none of the three existing
window tests notice that mutant. `mcp_server.py` and `document_reads.py` stay at
100% line coverage over the MCP test files.

**Things learned.** A `kind` filter does not move a payment's canonical
document. On the fixture the receipt holds the slot, so `kind='invoice'` matches
the invoice alone and returns no rows, with `excluded: {duplicate_payment: 1}`:
the payment is real but counted under its receipt. Run once by hand; coverage
discloses it and this tool does not change it, but an answer to "what did our
invoices come to" has to read that block.

**Not done.** Spending over time and saved charts are still not MCP tools (plan
non-goal). Unknown tags, projects and matters still match nothing silently;
only `kind` is checked, because the concept-word mistake is the common one.

## 8. Review fixes

An independent review of W9 and W10 found one critical and five lesser
defects. Commit `4f77222` fixed the W9 ones; this one fixes W10's three.

**Semantic search output (`4f77222`).** At `limit=50`, `semantic_search` could
return about 270 000 characters of passages, past the tool-result cap of MCP
clients (Claude Code's default is 25 000 tokens). `limit` now stops at 20, and
one call's passages share a 24 000-character budget, split evenly across the
hits: whole passages while they fit, the next one shortened, and
`passages_truncated` says so. `embed_texts` now wraps a `ValueError` or
`TypeError` from a malformed 200 (a proxy's HTML page, a bad vector) into
`EmbeddingError`, so callers fall back as designed instead of a masked
error. Doc gaps closed: the `passages` description no longer says an empty
list means "not indexed" (it also means only the keyword ranking found the
document), the `get_document` row names `review_findings`, and the
`money_query` row notes the 25-id citation cap.

**Dedup within the filter (critical).** §7's "things learned" was a bug, not a
property. `sum_amount` gated on the view's archive-wide `is_canonical`, so a
filter that matched only a payment's non-canonical twin (`kind='utility-bill'`
when the receipt holds the slot, or a tag only the bill carries) returned no
money for that payment and reported the bill as `duplicate_payment`, which the
tool text explains as "counted once". The lead's decision: choose the counted
document among the documents that passed the filters and the earlier gates,
with the view's own ordering. `_counted_per_payment` does that with a window
over `spend_facts` rows, as a FROM subquery so SQLAlchemy cannot correlate its
`Document` away. When grouping, the group key is part of the candidate set, and
a document lacking the key is `duplicate_payment` when a twin with the key
carries its payment, `no_sender`/`no_kind` only when none does. A side effect
over the whole archive: a payment whose view choice is an undecided document
(lower id, NULL kind) now counts through its `payment_due` twin instead of not
at all. The charts, the facets endpoint and the spending API still read the
archive-wide flag; moving them is a follow-up (money-facts.md §5.2).

**Blank and unknown filters.** `tags=[tag, ""]`, `facets={"category": ""}`
and `sender=""` were valid MCP arguments that gave a silent zero (or, for the
sender, no filter at all). Two options: move Ask's `_text_arg`/`_slug_args`/
`_facet_args` somewhere shared, or constrain the MCP filter types. Chose the
constraints: one `FilterText` type (stripped, at least one character) used by
every shared filter, so one rule covers all three filtered tools; it shows in
`tools/list` as `minLength`; and a refusal tells the model its argument was
wrong, where Ask's dropping would quietly widen the filter. Ask keeps its
helpers because its tool loop has no schema layer. `money_query` alone also
refuses unknown tag, project and matter slugs and unknown facets or facet
values, naming each and pointing at `get_library_context`: a zero there is a
total. The search tools keep answering an unknown slug with `total: 0`, which
reads as "nothing matched", not as an amount.

**Row cap.** `distinct_senders` and grouped `sum_amount` returned one row per
sender (and currency), uncapped. Ask feeds the same rows to its model, so the
cap is in `structured_query` (`MAX_GROUP_ROWS = 50`), not in the MCP tool. The
largest groups are kept, by |total| for sums (a refund-only sender of −90 beats
+5) and by document count for senders, and the cut groups' documents become an
`over_limit` reason, so coverage still partitions and `needs_review` counts
only what the rows show. `COVERAGE_GUIDE`, which both tools quote, says so.

**Evidence.** Every new behavioural test was seen red on the old code, with
the old number in its comment (the reproduction gave 18.75 for a hand-computed
102.95; bill-only tag, no rows for 84.20; receipts 84.20 for 159.70; the
review-status case 63.10 for 147.30), except the two that guard what already
worked (both twins in the filter, a same-kind tie), which hand-made mutants
turned red: count every twin (338.15 for 178.45), drop the `payment_made` or
the has-lines preference, reverse the id tie-break. Twenty mutants across the
three fixes, all killed in the end (the tie-break one survived until the tie
test was added). `mcp_server.py` stays at 100% line coverage over the
MCP test files. No existing test changed.

**Second review of the dedup fix.** Ranking inside the filtered set had let
the date window choose the twin. A bill dated 28 September and its receipt
dated 3 October counted 100.00 in a September total, 100.00 in October and
100.00 over both, so month totals did not add up, and the charts (which use
the archive-wide flag) disagreed with Ask and MCP. The lead's rule: every
filter except the date window decides which document carries a payment;
the window then applies to that document. `_payment_choices` ranks over
`filter_conditions` with `date_from`/`date_to` cleared. A matched document
whose payment is carried by a twin outside the window (or by an undated
twin, which no window holds) is the new reason `payment_dated_elsewhere`.
`kind='utility-bill'` still chooses among bills only, so the bill carries
the payment in September. `COVERAGE_GUIDE` now says that adjacent date
windows add up, but totals under different non-date filters can share
payments and must not be added together.

The same change answers the performance finding. The ranking used to appear
about six times in the grouped coverage statement, plus two correlated
`EXISTS` against `spend_facts`. It is now one `MATERIALIZED` CTE, one row
per candidate document, carrying the chosen twin's date, key and currency.
Every coverage reason, and the row query's own gate, is a membership test
against that CTE. The emitted SQL has one `WITH payment_choices AS
MATERIALIZED` per statement, referenced once by the row query and five
times by the coverage query, with no `EXISTS` on `spend_facts` left. A
quick probe (4,000 documents in 2,000 merged pairs, 100 senders, after
`ANALYZE`) timed the new `sum_amount` at 2.14 s ungrouped and 2.13 s
grouped by sender, against 3.39 s ungrouped for `cb56306`. The probe was
stopped before the old grouped figure came in. Without `ANALYZE`, both the
old and the new statement ran for more than 15 minutes on freshly inserted
rows: the planner, with no statistics, re-evaluated the view once per
document. Production autovacuum analyses, but that is a cliff worth knowing
about.

The row cap moved onto the same data. A twin whose payment lands in a row
the cap cut is now `over_limit`, not `duplicate_payment`, because its payment
is in no row (the probe's 50 cut senders reported both twins of each of
their 1,000 payments as `over_limit`).

**Follow-up, recorded in money-facts.md §5.2, out of this run:** a `facets`
filter checks a document's own labels, but `sum_amount` sums every line row
of the carrying document, so `category=energy` on an itemised invoice also
sums its water line. This predates the review.

Evidence: September plus October equals the two months together (40.00 +
85.00 = 125.00; before the fix 140.00 + 125.00). The kind-filtered guard
(bill 100.00 in September, credit note -15.00 in October) and the cut-row
twin (both `over_limit`; before, one was reported as a duplicate) are new
tests. Seven mutants all turned a test red: ranking inside the window,
ranking without the kind filter, no `COALESCE` for an undated carrier, the
group key not ordered first, a cut row's twin left as a duplicate, NULL
currency not coalesced, and the window not applied to the chosen document.
