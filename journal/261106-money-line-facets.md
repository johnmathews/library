# Money totals filtered by facet count the lines that carry it

> Purpose
>
> Records why a facet filter on a money total summed the wrong rows, the
> rule now in `sum_amount`, and how it was proven. Work unit W4 of the MCP
> follow-ups run (findings F7 and F8).

**Date:** 2026-11-06
**Branch:** `eng-mcp-followups-money`

## 1. The defect

`sum_amount` serves Ask's `query_documents` and MCP's `money_query`. It
applied a `facets` filter through the shared document filter, which reads
`document_labels` only, with two wrong results:

- **Every line counted.** An itemised document labelled X summed all of its
  lines, including lines overridden to Y. In a scoping probe, 60 + 40 gave
  100.00 where the chart gave 60.00.
- **Line-only facets were missed.** A facet that only a line carried
  matched nothing, while the chart for the same facet counted the line.

Charts were right throughout. They filter on `spend_facts.labels`, which
merges a line's own labels over its document's, row by row.

## 2. The rule

- **Facets match money rows.** A row matches when its labels contain every
  facet asked for (`labels @> facets`, the AND across facets, held on one
  row). A row is a whole unsplit document or one line.
- **Facets leave the document filter.** They are applied instead to the
  twin-choice candidates and to the counted rows. The counted twin is then
  chosen among documents whose money carries the facet, and only the
  matching lines of that document are summed.
- **Coverage `matched`:** a document matches if one of its money rows
  matches, or, when it has no amount and so no money row, if its own labels
  do. The broader rule, "its labels or any row", was tried and rejected. A
  document labelled X whose every line is overridden to Y would be matched
  but would fall in no coverage reason, and `included + excluded ==
  matched` would no longer hold. A mutant proved this.
- **Search is unchanged.** `search_documents`, `semantic_search` and the
  documents list keep document-level matching. That was the user's choice
  at the plan gate ("money only").
- **The view gains a declared column.** `models.py` declares `labels` on
  the `spend_facts` table object, so the predicate is an ORM expression and
  not a string.

## 3. Proof

The fixture is deliberately adversarial:

- an itemised invoice labelled services, with lines 60 (inherits) and 40
  (overridden to supplies);
- its receipt twin, labelled supplies;
- an unlabelled document with a 10.00 services line and a 20.00 unlabelled
  line;
- February dates throughout.

| Facet | Before | After |
| --- | --- | --- |
| services | 100.00 | 70.00 |
| supplies | 100.00 (the receipt's total) | 40.00 |

Six tests failed on the old code. Both facets now equal `chart_series` for
the same rule, and they are stable across services → supplies → services.
Five hand-made mutants were each killed:

1. the facet left out of the candidates;
2. the facet left out of the counted rows;
3. the broader matched rule;
4. matching on money rows only;
5. testing only the first facet.

Two of the tests are pins, green before the change: the amountless
document reported as `no_amount`, and the twin chosen by its money's
facet. The full backend suite passes.

## 4. Still open

When only the *non-canonical* twin carries a facet, `sum_amount` counts the
payment and the chart does not, because charts read the view's archive-wide
canonical flag. That is finding F4, and work unit W5 addresses it by merging
twin labels in the view. MCP's facet field description still says "a
document must carry all of them". W6, which edits `mcp_server.py`, rewords
it for `money_query`.
