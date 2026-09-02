# Two ways a chart said "you spent nothing"

**Date:** 2026-09-02
**Branch:** `et/ux-issues-20260902`
**Issue:** [#127](https://github.com/johnmathews/library/issues/127) (the two user-facing items)

## 1. The issue was stale, and its suggested fix was already ruled out

#127 says two `in` clauses on one facet are "ANDed into a permanently empty
chart" and proposes "merging same-facet clauses by intersection in
`filter_drafted_rule`". Both halves were out of date before this work started.

`_refuse_unmatchable_conjunction` shipped on 2026-09-01 with the rule editor
(#135/#141) and refuses the shape on all four paths that call `_validate_rule`.
And `charts.md` §13 rules the merge out explicitly — merging turns the AND into
an OR, which answers a different question and moves money *into* the chart.

So the work here is not what the issue asks for. It is the **remnant the issue
conceals**: a fifth path.

## 2. The fifth path

`POST /spending/draft` does not call `_validate_rule`, and its sibling documents
why — `filter_drafted_rule` has already guaranteed that every surviving clause
names vocabulary that exists.

That reasoning is about **membership**. It says nothing about whether the
surviving clauses can be **combined**, and `filter_drafted_rule` appends one
clause per drafted clause with no same-facet check. So a model drafting
*"software and services spending"* produced:

1. `unknown_terms == []` and `expressible: true`;
2. a **200 preview of an all-zero chart** — the "you spent nothing" reading §12
   exists to remove;
3. a **422 the moment the owner pressed Save**.

The owner was shown an answer, told it was fine, and then refused, with nothing
in the preview to predict it.

## 3. Drop and report, not refuse

The fix keeps the draft path's own contract rather than importing the editor's.
Every other undraftable input there is dropped and reported (§9's table); a 422
on a plain-language question is not the answer that surface exists to give. So
the second `in` clause is dropped, the first survives, and the drop is reported.

**Dropped, still not merged.** Keeping the first clause is narrowing; merging
would widen. That is the same reasoning that makes the refusal a refusal, and
it is now pinned by a test that reds specifically when the merge is
implemented — so a future reader acting on #127's text as written gets a red
suite rather than a silently different chart.

### The reporting channel needed splitting

`unknown_terms` is rendered by the API under `"not in the vocabulary: ..."`. A
clause dropped for being uncombinable names **only vocabulary that exists**, so
reporting it there would have explained a real drop with a false reason — the
same failure as the empty chart, one layer up.

So `DraftResult` gained `unmatchable_terms` beside `unknown_terms`, and the
route composes its message from the two causes separately. They still merge into
one list on the wire, because the frontend renders that list as neutral chips
and already has a "partly expressible" state that does exactly the right thing.
No frontend change was needed.

## 4. `/cell` answering 0.00 to an impossible question

Same class, different route. `/cell` validated `period` but not `split_value`,
so a stale or hand-edited value made `_CELL_NARROWING`'s `IS NOT DISTINCT FROM`
false for every row and the panel came back `total: "0.00"` with no payments.

Two checks, both decidable from the resolved split axis alone:

- **an unsplit chart with any non-null `split_value`** — its split expression is
  `CAST(NULL AS text)`, so nothing can ever match. Free, exact, and a case #127
  did not mention;
- **a `split=sender` value that is not a `senders.id`-shaped integer.**

### The check that was deliberately not written

Validating that the value *exists* is the tempting third check and it would be
wrong. A facet value can be deleted from the vocabulary while
`spend_facts.labels` still carries it on already-labelled rows — so that bucket
**is** drawn by `/data`, and refusing it would reject a cell the chart had just
rendered. `_resolve_splits` already falls back to the raw value rather than
raising for exactly this reason. The reverse holds too: a sender that exists but
contributed nothing to the window is an honest empty bucket.

Existence of the value and existence of the bucket are independent in *both*
directions. The only exact test is the set of `split_value`s `chart_series`
returned for that period — which means running the series, the one thing this
route exists not to do. So only the shape is judged.

The sender check **parses** rather than pattern-matches, so `"007"` still
resolves to its sender; a guard tightened to ASCII digits reds a test written to
catch that over-correction.

## 5. Four assertions rewritten on purpose

`_MALFORMED_SPLIT_VALUES` parametrized seven inputs asserting 200-with-raw-label.
Four of them now 422. That list is split into `_REFUSED_SPLIT_VALUES` and
`_PARSEABLE_SPLIT_VALUES`, keeping the per-class reasoning that explains why each
input is there.

The property the list was originally built for — **none of them may 500** — is
untouched, and a 422 honours it as fully as the old 200 did. Recorded here
because a rewritten assertion should never look like a deleted one.

## 6. Every behavioural claim was mutation-checked

None of these tests were trusted because they passed. Each fix was reverted and
the suite watched:

| mutation | result |
| --- | --- |
| neuter the same-facet drop | both pure tests red, both route tests red |
| implement #127's intersection merge instead | the merge-specific test red, its sibling green |
| drop the unsplit-chart branch | its own test red, nothing else |
| neuter the sender-id parse | all four refused-value cases red, the three parseable ones green |

The second row is the one worth keeping: it is what distinguishes "the clause is
gone" from "the clause was folded into the other one", and only one of the two
tests can tell.
