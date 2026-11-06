# Planner statistics after bulk money writes

> Purpose
>
> Records the check that settled whether a money question asked straight
> after a bulk import can hang, and the fix that followed. This is work unit
> W7 of the MCP follow-ups run (finding F18).

**Date:** 2026-11-06
**Branch:** `eng-mcp-followups-analyze`

## 1. The claim

The previous run saw the money statements run past 15 minutes on rows that
had just been inserted and not yet analysed. F18 inferred from this that a
fresh `library import paperless` followed by a money question would hang
until autovacuum got to the new rows.

## 2. The check

A probe built about 4,000 synthetic documents in a throwaway Postgres
testcontainer. They formed 2,200 payments, 1,800 of them merged, with
itemised lines and labels. Autovacuum was off on the money tables, and each
statement had a 120 s timeout. The probe script stays in the session
scratchpad and is not committed.

| Scenario | `sum_amount`, no filter | with a facet |
| --- | --- | --- |
| Never analysed, amounts decided | timed out at 120 s | timed out at 120 s |
| After `ANALYZE` | 1.34 s | 7.03 s |
| Import only (amounts still NULL) | 0.035 s | 1.55 s |
| Backfill onto stats taken while amounts were NULL | 7.1 s | 7.4 s |

- **The plans.** With no statistics, the planner expected about 43 rows from
  the recursive `payments` step and chose 37 nested loops. With statistics it
  expected about 56k rows and chose hash joins.
- **Autovacuum.** Left on, it analysed every money table larger than 50 rows
  within 45 s.
- **The verdict.** The cliff is real, but the window is narrow. It needs
  tables that have never been analysed and amounts that are already decided.
  The import writes amounts as NULL. The amount backfill decides them, so
  the backfill is the write that matters.

## 3. The fix

- **The helper.** `library/money/statistics.py` holds `MONEY_TABLES` and
  `analyze_money_tables`, which commits and then runs a single
  `ANALYZE <tables>`.
- **The amount backfill** calls it once it has classified at least one
  document.
- **The paperless import** calls it once it has imported at least one
  document, and never on a dry run.

A pin walks `pg_depend` from both money views and asserts that every base
table they read is in the list. Removing `line_labels` by hand turns it
red, so a view that starts reading a new table cannot be left out. The
call-site tests were seen red with the helper disabled. The "no ANALYZE"
tests were seen red with their guards forced true. `docs/money-facts.md`
§6.2 documents the measurements.

## 4. Side finding

While timing the statement, the probe found that W4's first version made
facet-filtered `sum_amount` about 5× slower, 1.34 s on `main` against
6.5 s. Its coverage `EXISTS` re-ran the recursive payments view for each
document. That was fixed in W4's PR before it merged.
