# The chart engine meets the live archive

**Date:** 2026-08-30
**Branch:** `charts-engine` → PR #121, squash-merged as `b32a67c`, deployed; prod
alembic head `0036`.

## What shipped

Ten tasks, twelve commits, +20 tests in a final fix wave for a suite of 2182.
The engine is one relation and three functions over it:

- **`spend_facts`** (migration `0035`) — the single relation every chart reads.
  One row per counted document, or one per spend line where a document is split,
  carrying the canonical document's identity so a merged invoice/receipt pair
  contributes once.
- **The write path** — `spend_lines` and `line_labels`, with
  `sum(lines.amount) = documents.amount_total` enforced by one plpgsql function
  bound to **two** deferred constraint triggers: on `spend_lines`, and mirrored
  on `documents (AFTER UPDATE OF amount_total)`.
- **`charts`** (migration `0036`) — a saved question: a name, the owner's words,
  a serialised rule, a default grain and split, and a display currency.
- **`charts/rule.py`, `query.py`, `footer.py`, `draft.py`** — rule translation,
  the series and the drill-through cell, the eight-category footer, and LLM rule
  drafting that can only ever emit values already in the facet vocabulary.
- **Ten routes** under `/api/spending`.

The design is [`docs/superpowers/specs/2026-08-28-charts-redesign-design.md`](../docs/superpowers/specs/2026-08-28-charts-redesign-design.md);
what the code does is [`docs/charts.md`](../docs/charts.md).

## The step that decides

The previous branch in this series, PR #115, passed every gate, deployed clean,
and classified nothing. So the verification that matters is not the suite. It is
the archive.

Migrations `0033`→`0036` applied cleanly. One pre-deploy `SELECT` earned its
keep first: `0034` adds a **validating** `CHECK` on `amount_kind`, which scans
every row and aborts the whole upgrade on a single out-of-vocabulary string —
and `amount_kind` had lived as a bare `varchar(16)` with no constraint for the
whole of `0033`'s life, while `test_migrations` only ever runs the cycle on an
empty database. Every live value was inside the eight. It applied.

### The credit note

```
library backfill-amounts → classified 2, empty 1, skipped 0
```

Refund count `0 → 1`. **The archive's one credit note resolved to `refund`.**

That single sentence is the whole point of the exercise. Money moving back had
no representation at all until this series; the classifier declined to force it
into a kind that meant the opposite; the design argued the sign question out
in #117; and the only way to know whether any of that reached the archive was
to run it against the archive. #115's failure mode — green, deployed, inert —
did not recur.

### Nothing merged away

Payments **242 from 259 documents**, identical to the pre-deploy baseline. The
sign guard added in `0034` prevents a refund from merging with the invoice it
reverses, and the plan's own alarm — "a drop means the sign guard is merging
things it should not" — did not fire. The refund's group size is 1 and it is
canonical, so it contributes: signed and unsigned totals differ by exactly twice
its magnitude, which is the arithmetic of a number that *lowers* a total rather
than one that was dropped or added.

The documented override hole (below) was checked rather than assumed absent:
**mixed-sign groups = 0**.

The view's own accounting agrees with all of that from a different direction.
`spend_facts` holds 176 rows — exactly the amount-bearing document count — of
which 159 are canonical. 176 − 159 = **17** non-canonical rows; 259 − 242 = **17**
documents collapsed by merging. Two numbers computed by unrelated SQL, equal.

### The three questions

Run through the real engine in-process against the live database —
`chart_series` + `chart_footer` + `rule_predicate`, not hand-written SQL over
the view:

| chart | rule | grain | cells | payments | documents |
|---|---|---|---|---|---|
| AI subscriptions | `category IN (software) AND cost_type IN (subscription, usage)` | month | 3 | 23 | 27 |
| Accountancy | `category IN (accountancy)` | year | 7 | 23 | 23 |
| EV charging | `category IN (ev-charging)` | month | 2 | 13 | 14 |
| All spending | *(empty)* | year | 9 | 142 | 158 |

`sum(cells) == total` held on **all four** — §2.5's promise, live.

Cross-checked against hand-written SQL over `spend_facts`: accountancy matched
exactly, 23 from 23, single-currency and with no merges to disagree about. The
AI-subscriptions count *exceeded* the hand-written figure, and the difference is
the feature working: the SQL filtered to one currency, while the engine converts
the rest at each document's own date rather than dropping them. `unconvertible`
was empty everywhere.

### The footer reports real money

The plan was blunt about this: "a zero here almost certainly means the query is
wrong, not that the archive is perfectly labelled."

- `uncategorised` — 5, 2 and 2 documents on the three facet-bearing rules; and
  `None` on the empty rule, which is correct: a rule naming no facet cannot have
  a labelling gap.
- `unclassified` — 1 document. The one remaining undecided amount-bearing
  document is *visible*. This category only exists because the pre-flight scan
  found that a document with an amount but an undecided kind fell through every
  other bucket and appeared nowhere; on real data it immediately had something
  to say.
- `undated` — 2 documents. Dateless money is reported, not silently dropped.
- `excluded` — `balance` 5, `coverage_limit` 4, `estimate` 5. Reported, not
  counted. The insurance-ceiling problem that started this whole series is
  solved and *shown to be solved* in the same breath.
- `netted_refunds` — refund count 1, netted in the header block rather than
  filed under "excluded", because a refund is spending, with a sign.
- `unaccounted` — `None` on every chart. That is the live `ELSE` bucket, and an
  empty one is exactly what a healthy archive should produce.

The ten routes were finally enumerated from the running container rather than
from the source — which is also how a ruling of mine got corrected: I had ruled
"nine routes, not seven" from a plan whose own list enumerated ten.

## What executing found that reading would not

This is the part worth keeping.

**Three claims the plan labelled "executed against PostgreSQL 17" were false in
this repository.** Not subtly wrong — each one fails on the first run:

1. The single multi-statement `op.execute` cannot work here. Alembic runs over
   **asyncpg** in this repo (`migrations/env.py`), which prepares every
   statement: *"cannot insert multiple commands into a prepared statement"*. The
   plan's claim was true under psycopg, which this repo does not use for
   migrations. Split into three calls.
2. A plpgsql `RAISE EXCEPTION` surfaces under asyncpg as a bare
   `sqlalchemy.exc.DBAPIError`, **not** `ProgrammingError`. Composite-FK
   violations *do* map to `IntegrityError`, which is what made the wrong claim
   plausible.
3. `SpendLine.origin` needs `values_callable`, or SQLAlchemy persists `MANUAL`
   against the migration's own lowercase `CHECK` and **every insert is
   rejected** — proved by removing it: 9 of 13 tests fail.

**A NULL-currency document would have 500'd every chart route.**
`Document.currency` is nullable and `amount_currency_coupling` is a *warn*, not
a block, so an amount-bearing, dated, summable document with no currency at all
is a permitted live state — and the code either raised `ValidationError` or a
`TypeError` from sorting `str` against `None`. A dead page, not a wrong number.
The archive happens to hold none today; the state is still reachable, so the
value is now reported through the `unconvertible` channel it belongs in.

**All twenty-two of one task's tests passed with the drill-through converting at
the wrong date.** The panel behind a bar is required to convert each amount at
*its own* document date, and the series has a dedicated test forbidding
anything else — but the cell's conversion was a *copy* of the series' loop, and
the mutation that broke it stayed green because the fixture was single-currency
and `convert_amount` short-circuits when the currencies match, so the date
argument is never read. The test that looked like the guard could not have
failed.

**Twelve tests passed with the split axis rendered completely inert.** Changing
the split changes only how a total is *divided*, never the total, so a split
that does nothing still satisfies every assertion about the total. The two
mutations now fail on different assertions — one on the total, one on the bucket
set — which is the signature of two independent guards rather than one
assertion doing both jobs.

**The trigger the whole invariant rests on was uncovered.** The mirror trigger
on `documents` was proven load-bearing at once, but the `spend_lines` binding
itself had no mutation that reddened until a fix round added one.

And a general one, since the same shape appeared again and again: a mutation
that fails for the wrong reason is not a mutation check. Appending a split
predicate unconditionally left a bind parameter unbound and killed eleven tests
with a `StatementError` — which proves nothing at all about the split.

## Five times a structural fix beat a test

If this entry has a thesis, it is this. Each of these started life as a review
finding of the form "add a test for X", and each was closed instead by removing
the thing that made X possible:

1. **`_GRAIN_SQL` deleted.** A dict restating the `Grain` enum's own values, of
   which only one was ever exercised; a transposed entry (`WEEK: "month"`) would
   have passed every test and misbucketed every week, quarter and year chart.
   Binding `grain.value` directly makes the transposition *unrepresentable*.
2. **Four footer queries collapsed into one `CASE`.** One statement assigning
   exactly one bucket per row, so "nothing falls through" is a property of the
   shape rather than of the tests. With a sting in the tail: I ratified this
   from the implementer's description without reading the SQL, and the `ELSE`
   arm was then *deleted by a `WHERE` clause downstream* — a safety net that
   caught nothing. Renaming it `unaccounted` and letting it through the filter
   made the dead branch live, and routing a seeded shape into an unknown bucket
   now surfaces it in the balance instead of losing it.
3. **`_converted()` extracted.** One definition of the sign-and-convert path,
   read by both the series and the cell. The payoff was immediate and free: the
   wrong-date mutation now fails *two* tests, because a shared definition turns
   one guard into two.
4. **`narrowing: str` became `cell: bool`.** A free-form SQL fragment appended
   after `AND (...)` — where a value of `" OR TRUE"` would have defeated the
   entire `WHERE` — became a flag the function interprets itself. Safe by
   construction rather than by usage.
5. **The period boundary derived from the database.** The API validates that a
   drill-through `period` sits on a grain boundary; a Python `_period_start`
   reintroduced a second definition of the time bucket, in a different language,
   right after (1) had removed the first one. It is now generated from the same
   template as the SQL expression, so the boundary the API validates against
   *is* the expression the chart bucketed with.

The pattern behind all five: when a review says "this could drift, add a test",
ask first whether the two things that could drift can be made one thing.

## Two spec sentences the branch corrected

Both by execution, and both in the spec's §5.

**§5.1's GIN index.** "A GIN index on `labels` serves both" is not merely
unhelpful, it is not buildable: `labels` is computed by `jsonb_object_agg`
*inside* the view, so there is no stored column, and Postgres rejects
`CREATE INDEX` on a view outright. The honest replacement is a measurement: a
sequential scan on `line_labels` does appear at 1,800 rows, but at 36,000 rows
the planner picks the primary key with no new index present, and adding one
moves 98.0 ms to 97.7 ms for 728 kB. Measured, and **declined**.

**§5.2's closing paragraph.** It said the `deleted_at IS NULL` filter inside the
view is redundant and "the guarantee comes from the join, so the join is what
must not be optimised away". Mutation says otherwise. Removing the join did
*not* redden the deleted-twin test — five **merge** tests went red instead — and
removing the filter alone changed no result at all. `payments` seeds its
recursion from live documents and every edge arm filters `deleted_at` on both
endpoints, so **the filter and the join each independently exclude deleted
documents; neither is "the guarantee"**. The join's indispensable role is
payment **identity**: remove it and the partition degenerates to one partition
per document, silently un-merging every payment in the archive. That is a
stronger reason to keep it than the one the spec gave.

## Known limits now live

- **A payment group with two signs.** The sign precondition sits in the `pairs`
  CTE, which the manual `MERGE` override arm never passes through — so a human
  merge of two undecided documents followed by a `backfill-amounts` run that
  classifies one as `refund` produces a group whose sign is undefined, and only
  the canonical document's sign reaches a total. Applying the guard to the
  override arm would silently undo a human's explicit correction, which is
  worse. It is a stated limit, not a fix, and it is checked live: zero today.
- The rest are enumerated in [`docs/charts.md`](../docs/charts.md) §13 and
  [`docs/money-facts.md`](../docs/money-facts.md) §5 — the unmerged pair the
  footer cannot report, the upper-bound `documents` count, the window-scoped
  `unclassified` backlog, and two `in` clauses on one facet.

## Follow-ups filed

- **A fifth writer of `amount_total`, unguarded.** `src/library/ask/engine.py`
  calls `apply_document_update` and commits without translating the trigger's
  `P0001`, and its `_WRITABLE_FIELDS` includes `amount_total` — so on the first
  allocated document, an Ask turn that corrects an amount fails as a 500 with a
  poisoned session rather than a named 400. Pre-existing, and unreachable until
  the archive has its first spend-line allocation (it has none). The fix is to
  reuse the same SQLSTATE helper the other writers use.
- **Extraction's partial skip.** Re-extraction now skips `amount_total` on an
  allocated document, but still writes `currency` and `amount_kind` — so a
  re-read that finds a different currency leaves the currency disagreeing with
  the lines' denomination, and `amount_currency_coupling` will not fire, because
  both fields are set. The clean form is to skip `currency` alongside.
- **`skipped_fields` is invisible.** It is recorded in `extra["extraction"]` but
  not in the `extraction_completed` event detail, which is the surface the
  timeline actually renders. Today the owner is told nothing.
- **The vocabulary's fifth and fourth surfaces are unguarded.** The claim that
  the `amount_kind` vocabulary's five surfaces move together is true of three:
  the test compares the enum, the extraction schema and the migration's list.
  **Neither classifier prompt is compared by anything**, so both can drift from
  the vocabulary silently.
- **The docs-stamp check has now been bitten twice by date arithmetic.** The
  first time was `--since=<bare date>`, which means that date *at the current
  clock time* and therefore oscillates daily. This time the squash-merge commit
  landed dated a day after every stamp written during the work, so `main` went
  red on a check that had passed on the PR, on documents whose covered code had
  not moved at all. It will recur on any PR that stamps docs and merges across
  midnight UTC. Fixed honestly this round — re-verified and re-stamped in #122
  rather than bumping a date — but the check wants a fix that does not depend on
  when a merge button is pressed.
