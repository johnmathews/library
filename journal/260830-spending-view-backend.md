# The spending view's backend

**Date:** 2026-08-30
**Branch:** `spending-view-backend`

## 1. What shipped

Plan 4a of the spending-view work, eight tasks on top of the chart engine
(`b32a67c`): a nullable, CHECK-enforced `colour` on both split axes
(`facet_values` and `senders`, migration `0037`); `colour` threaded onto every
read surface (`GET /api/facets`, `GET /api/senders`, `DataOut.splits`,
`CellOutBody`); write surfaces for it (`ValueRename` became `ValuePatch`,
and a new `PATCH /api/senders/{id}`); `GET /api/spending/{id}` to load one
saved chart directly; split-value resolution so `/data` and `/cell` answer in
sender names and facet labels instead of raw ids; a drill-through route,
`GET /api/spending/{id}/footer/{bucket}`, for the documents behind a footer
count; and `GET /api/facets/counts`, the aggregate the empty state proposes
charts from.

## 2. Two questions settled by executing them, not by reasoning about them

Two things this plan needed to know were the kind of question a diagram gets
wrong: whether a split spend-line document contributes one row or two to a
footer bucket, and whether reading `spend_facts` for a facet-counts aggregate
needed its own moneyless-proposal filter on top. Both were run against a real
Postgres before the plan was written, and both settled the opposite of the
naive first guess.

**The split-row question.** `_CLASSIFY_SQL` selects per `spend_facts` row, and
`spend_facts` already has one row per spend line where a document is split
(the chart engine's own design). So a `100.00` document split `60.00`/`40.00`,
neither line labelled, under a rule naming `category`, produces **two**
`uncategorised` rows sharing one `document_id`:

```
{'document_id': 1, 'amount': Decimal('40.00'), 'bucket': 'uncategorised'}
{'document_id': 1, 'amount': Decimal('60.00'), 'bucket': 'uncategorised'}
FOOTER uncategorised -> amount=Decimal('100.00') documents=1
```

`Footer`'s own `_Group.documents` is a `set[int]`, so the *count* was already
distinct-by-document — reasoning from that alone would have concluded the
*list* needed no deduplication either, which is exactly wrong. Two
consequences reasoning would have missed followed directly from the run: the
drill list in `chart_footer_documents` has to deduplicate by `document_id`, or
its length disagrees with the count sitting right above it in the same
response; and a listed document's `amount` has to be the *sum* of its rows in
that bucket (`100.00`), not one row's (`60.00`) — the number `60.00` is not
in the footer's own accounting anywhere, so showing it as "the" amount for
that document would be printing a figure the accounting never produced.

**The moneyless-proposal question.** The open question for `GET
/api/facets/counts` was whether it needed an explicit filter to keep an
amountless or soft-deleted document's label out of the chart-proposal list.
Executed against Postgres, the answer was that reading `spend_facts` (rather
than `document_labels`) does this filtering **for free**: a labelled document
with a null `amount_total` produced no row, and a labelled but soft-deleted
document produced no row, because the view requires `amount_total IS NOT
NULL` and its join to `payments` excludes deleted documents. The only filter
that is *not* free is `is_canonical`, which the view does not apply on its
own — a merged twin is a second `spend_facts` row for money already counted
once, and it has to be excluded explicitly in the query.

Both facts are pinned by tests built from the exact shapes above:
`tests/test_chart_footer.py::test_a_split_document_appears_once_with_its_rows_summed`
for the first, and `tests/test_api_spending.py::test_a_value_with_no_money_behind_it_is_absent`
/ `test_a_merged_pair_counts_once` for the second.

### 2.1 A third mechanism, found by reading rather than by running

A related question came up during review of the facets-counts query and was
answered by reading rather than by another prototype: `is_canonical` and
`count(DISTINCT sf.document_id)` in `_FACET_COUNTS_SQL` look like they might
be doing the same job, and they are not. `is_canonical` deduplicates
**merged-twin documents** — two rows recognised as one real payment.
`count(DISTINCT ...)` deduplicates a *different* shape: one canonical
document split across spend lines emits one `spend_facts` row per line, and
`jsonb_each_text` produces one `(facet_key, value_key)` pair per row, so two
lines carrying the same label produce two identical pairs from the *same*
document. Removing either filter overcounts, on a different class of archive
data than the other one catches.

**Update, final fix wave:** this was recorded above as read-but-not-tested,
and a fix wave over the whole branch flagged that as a genuine gap — every
new test in this branch's Global Constraints owes a mutation check, and
deleting `DISTINCT` left all four of Task 7's counts tests green. Closed by
`tests/test_api_spending.py::test_a_split_document_counts_once_in_facet_counts`:
seed one document with a facet label, split it into two unlabelled spend
lines (they inherit the document's label per migration 0035's
`doc_labels || line_labels`), assert `documents == 1` from
`GET /api/facets/counts`. The mutation ran both ways — deleting `DISTINCT`
from `_FACET_COUNTS_SQL` turned the new test red (`2 == 1`) while the other
three counts tests stayed green, and restoring it turned the new test green
again. The split-line half of the non-redundancy claim is now proven by
execution, not read from the query's structure alone.

## 3. Extracting `chart_footer`'s whole per-row treatment, not just its SQL

The footer drill route (`GET /spending/{id}/footer/{bucket}`) needed the same
classification `chart_footer` already computes — the same `_CLASSIFY_SQL`
run, the same currency conversion, the same refund/exclusion sign convention
— filtered down to one bucket and shaped as a document list instead of an
aggregate. The plan's decision was to extract the *whole* per-row loop
(`_accounted_rows`: one execution of `_CLASSIFY_SQL`, one conversion call,
one sign decision) into a function both `chart_footer` and
`chart_footer_documents` call, rather than re-running the query a second time
in the new function with the filtering pushed into a second `WHERE` clause.

The reason to prefer extraction over a second query is the same reason
`footer.py`'s own module docstring gives for keeping the footer separate from
`query.py` in the first place: two independently-maintained SQL statements
computing overlapping things are two things a future refactor can put out of
step, silently, with no test positioned to notice — the exact failure mode
`footer.py` exists to prevent, now one level down. A second `_CLASSIFY_SQL`
would need its own bucket dispatch, its own conversion call, and its own sign
convention, and any of the three drifting from the original produces a drill
panel whose numbers do not add up to the bar it opened.

The mutation checks that came with this extraction proved the sharing was
real, not incidental:

- Removing the deduplication step reddened exactly the test built for the
  split-document shape (§2) and none of the others — a fixture without a
  split document cannot tell "deduplicated" from "not deduplicated" apart,
  which is a fact about the *test*, not the code, and is now recorded as such
  rather than left as a false sense of coverage from the other four cases.
- Renaming one `CASE` branch's bucket string reddened both an aggregate test
  (`chart_footer`'s own `uncategorised` assertion) and the new list tests
  together — proof that both functions are reading the same classification,
  not two copies that happen to agree today.
- The parametrised list-length test originally seeded only one excluded
  `amount_kind`, so a mutation that deleted the `amount_kind` filter on the
  `excluded` bucket had nothing to filter and passed unnoticed; fixed by
  adding a second excluded kind to the fixture, permanently.

## 4. `str.isdigit()` is not a safe gate in front of `int()`

Resolving a `sender` split's raw id (`/cell`'s `split_value`, a query
parameter and therefore entirely client-controlled) to a sender's name needs
to know whether the string is actually an integer. The first fix for the
unhandled `ValueError`/asyncpg-range 500 gated the `int()` call behind
`value.isdigit()`. That is not a safe gate: `str.isdigit()` returns `True` for
Unicode category-No "digits" that `int()` itself rejects — a superscript `²`
is the case that broke it — so a string can pass the `isdigit()` check and
still raise on the very `int()` call it was meant to protect.

The fix was to stop trying to pre-validate the string shape at all and just
call `int()` inside a `try`/`except ValueError`, then separately bound the
parsed integer against `senders.id`'s `int4` range before using it in a
query. `int()` is the only thing that reliably knows what `int()` accepts;
anything that tries to duplicate that judgement ahead of the call is a second,
weaker definition of the same rule, and the weaker one is what shipped first.
The general shape — a client-controlled string that must become a number
before it can be used safely — recurs in this codebase, and the working rule
now is: parse and catch, never pre-filter and then parse.

## 5. Eleven defects, all from plan text that had never been executed

Counting every `fix(charts)` commit and every `docs(plan)`/`docs(spec)`
correction commit on this branch gives eleven: five implementation fixes
found by code review after a task's tests were already green, and six
corrections to the plan or spec document itself, caught either by a
pre-flight read before implementation started or by executing a claim the
plan had only reasoned about (§2). All eleven trace back to something the
plan or spec asserted without having been run — an invented test fixture
idiom, a claim about what `split=None` means to a function nobody had
stepped through, an `isdigit()` gate that looked obviously sufficient, a
`_Group.documents` set that looked obviously sufficient as a stand-in for "the
list needs no dedup" — never to a mistake made independently while turning an
already-correct plan step into code.

That is a lesson this repository has paid for before, on an earlier plan:
plan code must be executed, not read, and read-only review of a plan is
exactly the activity that lets a plausible-sounding but wrong claim survive
unnoticed until an implementer runs into it. The eleven-for-eleven split this
branch produced is unusually clean evidence for it — not one defect
originated in a correctly-planned step being implemented wrong.

## 6. Full verification

`uv run pytest -q`, `uv run ruff format --check .`, `uv run ruff check .`,
`uv run mypy` and `uv run python scripts/check_docs.py --max-violations 0` all
run green at the commit this entry describes; see the Task 8 report
(`.superpowers/sdd/2026-08-30-spending-view-backend/task-8-report.md`) for the
verbatim output of each.
