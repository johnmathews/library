# Ask answer trustworthiness

**Date:** 2026-08-26

## What changed

Findings #1, #2, #3 and #11 of the semantic-surface review
(`docs/superpowers/specs/2026-08-26-ask-semantic-quality-design.md`).

Every `query_documents` aggregate now returns a `Coverage` object alongside its
rows (`matched`, `included`, `excluded`, `needs_review`), and the system prompt
obliges the model to disclose a non-empty `excluded` or a non-zero
`needs_review` in its answer.

## Why

`sum_amount` filtered on `amount_total IS NOT NULL` and returned only rows. A
spend total over 14 of 22 bills was indistinguishable from a complete one — to
the model as well as to the user. The same shape appeared in three more places:
the quote exclusion, the sender inner join, and the hard-coded list limit of 50.

Separately, the archive already computed a trust signal it then discarded:
`extraction/validation.py` flags a document `needs_review` when the extracted
amount's digits are absent from the document text, and that document was summed
with exactly the weight of a verified one.

## Decisions

- **One uniform `Coverage`, not per-aggregate keys.** Three aggregates with
  three shapes means the model learns three shapes and the fourth aggregate
  invents a fifth. `excluded` is a reason→count map because two drops can
  otherwise threaten to apply to the same document — see the partition note
  below.
- **`needs_review` sits beside the coverage fields, not inside `excluded`.**
  Those documents *are* counted in `included`. Trust and completeness are
  different questions and the prompt treats them differently.
- **The model is told not to filter flagged documents out of a total.** Offering
  `review_status` as a filter creates an obvious way to make the caveat
  disappear by changing the number instead of reporting it.
- **The citation fix is keyed on the `_NO_ANSWER` sentinel only.** The prose
  fallback is load-bearing — an existing test covers an answer that names its
  source without `[#id]`. Removing it wholesale would regress that. The residual
  case (the model phrasing its own not-found answer in its own words, so the
  string never equals the sentinel) is recorded in `docs/ask.md` §1.10 item 8
  rather than papered over.

## A bug caught during implementation: exclusion reasons must chain, not gate independently

The first cut of `sum_amount`'s coverage computed each exclusion condition
independently, gated only on "has an amount". A document that was both a quote
and senderless (`group_by="sender"`) matched *both* `quote_not_spend` and
`no_sender`, double-counting it and breaking the very invariant this feature
exists to guarantee: `included + sum(excluded.values()) == matched`.

Fixed by rebuilding the exclusions as successive refinements of one `include`
chain — start from "has an amount", narrow to "and is not a quote" (unless the
caller is asking about quotes), then, only when grouping, narrow again to "and
has the group-by column". Each reason now means "survived every earlier gate,
fails this one", so the reasons are disjoint by construction. Regression tests
cover the reported sender+quote overlap and an amountless quote asserted to
land under `no_amount` only, never also under `quote_not_spend`.

## Also fixed: a pre-existing crash on `group_by="kind"`

The same fix uncovered a separate, pre-existing bug, unrelated to this work:
calling `sum_amount` with `group_by="kind"` and no quote filter raised
`InvalidRequestError`. The outer query's explicit join to `Kind` and the
`is_quote` subquery's own reference to `Kind` gave SQLAlchemy two `Kind`
references to reconcile; its auto-correlation logic resolved that by stripping
the subquery of its own `FROM` clause entirely, which is invalid SQL. This
existed on `main` before this branch — no test had ever exercised
`group_by="kind"` — and is fixed by explicitly correlating the subquery to
`Document` (`.correlate(Document)`), which does not change its semantics. A new
test exercises `group_by="kind"` for the first time.

This doesn't change any documented behaviour beyond making `group_by="kind"`
work at all, so it isn't called out in `docs/ask.md` beyond the coverage
section already describing `no_kind`.

## Not done

The period-attribution problem (#4) is untouched: `sum_amount` still filters on
`document_date` (the issue date), so coverage is honest about documents and
silent about periods — an annual settlement paid in one lump sum still lands
in the year it was issued, not spread across the year it covers. That is a
separate piece of follow-on work.

Answer-quality impact of the disclosure rule is unmeasured. The coverage block
and the prompt instruction are exercised by schema and string tests, not by
any answer-quality eval — see `docs/ask.md`'s stamp for this same caveat.
