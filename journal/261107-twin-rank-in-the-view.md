# The twin order lives in the view

> Purpose
>
> Records why migration 0042 exposes the `spend_facts` twin order as a
> `twin_rank` column, why `sum_amount` can rank a filtered subset by it
> without changing any total, and how that was proven. Work unit W3 of the
> Library follow-ups run (finding F5).

**Date:** 2026-11-07
**Branch:** `eng-twin-rank`

## 1. The duplicate

The documents of one payment are ranked in a fixed order: line-bearing
first, then `payment_made`, then the lowest id. The order was written twice:

- in SQL, in the 0041 `spend_facts` view, which computes it as `rank` and
  marks rank 1 `is_canonical`;
- in SQLAlchemy, in `structured_query._payment_choices`, which ranks the
  twins that pass a total's filters.

An edit to one copy and not the other would make chart totals and Ask
totals disagree without any error. The repo's rule is to delete the second
copy, not to test that the two agree.

## 2. The change

- **Migration 0042** recreates `spend_facts` with the existing rank as a
  last column, `twin_rank` (`bigint`). It holds one value per document per
  payment, the same on every line row, and `twin_rank = 1` exactly where
  `is_canonical` is true. No view depends on `spend_facts` (checked in
  `pg_depend`), so a plain `DROP VIEW` and `CREATE VIEW` is enough. The
  downgrade recreates 0041's SQL, copied verbatim.
- **`_payment_choices`** orders by `has_key DESC, min(twin_rank) ASC`. Its
  three restated terms are gone. Under a facet filter, the `facet_facts` CTE
  carries `twin_rank` in place of `line_id`, which nothing else read.
- **`models.spend_facts`** declares the new column. This was outside the
  plan's footprint, and the change cannot work without it.

## 3. Why no total moves

- **The order is total.** The id breaks every tie, so no two documents of a
  payment share a rank.
- **Restriction keeps it.** Ordering a filtered subset by an archive-wide
  total order picks the same first document as re-ranking the subset by the
  same keys. This is decisions D1 and D3 unchanged: the total still chooses
  within its filter.
- **The one key that could differ.** `_payment_choices` computed
  `has_lines` as `bool_or(line_id IS NOT NULL)` over the rows left by a
  facet filter, while the view uses all of a document's lines. The view
  emits, per document, either its one unsplit row (no lines) or only its
  line rows, never both. A filter can drop rows, but it cannot turn one
  kind of row into the other. So a document with any row left has the same
  `has_lines` as in the view. A document whose lines are all dropped is no
  candidate at all.

## 4. Proof

Five pins in `tests/test_structured_query.py` (`test_twin_pin_*`). Each one
merges two or three documents by hand into one payment, at three different
amounts, so the total alone names the carrying document:

- a kind filter that drops the canonical receipt;
- a tag filter that drops the canonical itemised bill;
- a facet filter where the canonical invoice is labelled services but both
  of its lines are overridden to supplies;
- a facet filter that keeps only one of the canonical invoice's lines;
- grouping by sender, where the canonical invoice has no sender.

The cases include `has_lines` ties (all itemised, and none itemised). In
each one the `payment_made` twin has the higher id, so ranking by id gives
another answer. The pins assert the rows, the cited document and the
coverage. They passed on the old three-term order and pass on `twin_rank`.

Red observations, each mutant undone straight after:

| Mutant | Red |
| --- | --- |
| `min(twin_rank)` DESC | all 5 pins (kind pin: 101.00 instead of 103.00), plus 8 older `sum_amount` cases and 4 `money_query` cases |
| drop `has_key DESC` | the grouping pin (no row instead of 93.00), plus one `money_query` case |
| order by id only | all 5 pins; the kind, partial-line and grouping pins only on their filtered assertion (kind pin under `kind='invoice'`: 101.00 instead of 102.00) |
| view: line rows carry `rank + 1` | the view test, plus 3 pins |
| view: unsplit rows ranked by id only | the view test, 4 pins, 3 older `sum_amount` cases and 3 `money_query` cases |

- **The view.** `tests/test_spend_facts.py` holds five twins created so
  that the id order is the reverse of the rank order wherever a key above
  the id decides, plus an undecided twin and an unmerged document. It
  checks that `twin_rank` is constant across the invoice's line rows, that
  the ranks are 1 to 5, and that `twin_rank = 1` holds exactly where
  `is_canonical` does.
- **The migration.** `tests/test_migrations.py` runs 0041 → 0042 → 0041
  → 0042. It checks that `twin_rank` is the only column added, after every
  0041 column. After the downgrade, the columns and `pg_get_viewdef` equal
  a database migrated straight to 0041, and after the second upgrade they
  equal the first. The 0041 test now upgrades to `0041` instead of `head`.

## 5. Speed

The harness was a scratch probe, not committed, built on the earlier W7
probe. It used 4,000 synthetic documents (1,800 merged pairs, 200
refunds, 200 itemised invoices; 4,300 `spend_facts` rows), ran `ANALYZE`,
then ran `EXPLAIN (ANALYZE, BUFFERS)` three times per statement. The
before and after sides were run alternately, three rounds each. Before
was 0041 and the old code, after was 0042 and the new code.
`jit_above_cost` is 100,000.

| Statement | Est. cost before → after | Median ms before → after | JIT |
| --- | --- | --- | --- |
| `sum_amount` rows, no filter | 76,146 → 76,115 | 2,079 → 2,245 | no / no |
| `sum_amount` coverage, no filter | 68,379 → 68,347 | 1,010 → 972 | no / no |
| `sum_amount` rows, facet | 74,523 → 74,523 | 2,153 → 1,920 | no / no |
| `sum_amount` coverage, facet | 94,183 → 94,183 | 926 → 937 | no / no |
| `sum_amount` rows, by sender | 74,927 → 74,925 | 1,801 → 1,913 | no / no |
| `sum_amount` coverage, by sender | 40,484 → 40,482 | 922 → 965 | no / no |
| `chart_series`, services | 37,094 → 37,094 | 900 → 1,106 | no / no |

- **Costs.** Every estimated cost moved by 0.05% or less, and no plan
  gained a JIT line. The only plan change is the window sort key, which
  shrinks to `payment_id, min(twin_rank)`.
- **Answers.** Every probe answer was identical on both sides.
- **Times.** The millisecond medians moved both ways on a machine at load
  average 6.4. The chart plan is identical node for node, so its 200 ms
  difference is noise.
- **Watch.** The faceted coverage statement sits at 94k, within 6% of
  `jit_above_cost`. A change that adds 6% to it will add JIT compilation
  to every faceted total.
