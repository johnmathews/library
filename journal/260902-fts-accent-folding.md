# Folding accents in full-text search

**Date:** 2026-09-02
**Branch:** `fix/138-fts-unaccent`
**Issue:** [#138](https://github.com/johnmathews/library/issues/138)

## 1. What went

A document whose text carried a diacritic could only be found by typing the
diacritic. The plain-ASCII spelling returned "No documents match your search" —
indistinguishable from the document not existing.

Migration `0039` installs `unaccent`, defines an immutable wrapper, and rebuilds
both generated tsvector columns through it. `library.search` folds the query the
same way at both `websearch_to_tsquery` sites. Both sides are required: folding
one alone moves the mismatch instead of removing it.

## 2. Measuring first changed the plan twice

The issue carried two cautions, and checking them against the live database
turned both around.

**"Available but not installed" implied a manual host step.** `unaccent` has
been a TRUSTED extension since PG13, and the application role `library` owns the
database — so `CREATE EXTENSION` needs no superuser and belongs *in* the
migration, where it is versioned, tested and reversible, rather than in a
runbook someone has to remember on the next fresh environment.

**"A rewrite of the documents table, not a cheap migration."** True in kind,
irrelevant in degree: `documents` is 263 rows and 616 kB of heap (17 MB with
indexes and TOAST). The rewrite is milliseconds. The issue was right to ask for
a measurement and right not to guess the answer; the answer is that this one is
free.

Neither of these could have been settled by reading the code.

## 3. The third surface the issue did not name

`search.py` also builds each result's snippet with `ts_headline`, against the
document text, using the tsquery. Folding only the query leaves `ts_headline`
with an accented source and an unaccented query.

I assumed that would cost a highlight. Running it showed something worse: with
the raw source, a query of `Skoda` against a document reading `Škoda` returns

```
De reparatie aan de
```

— the *leading fragment*, which does not contain the matched term at all. The
row would appear in the results with nothing on screen explaining why, in
exactly the case this change exists to enable.

So the snippet source is folded too, which yields

```
reparatie aan de <b>Skoda</b> is afgerond en betaald
```

**The trade-off, stated plainly:** the preview now renders `Skoda` where the
document says `Škoda`. That is a real cost and it is accepted deliberately — a
snippet's job is to show *why* this result matched, and the detail page remains
the source of truth for what the document actually says. It lands only on
documents that carry a diacritic; for every other document the folded and
unfolded sources are byte-identical. Both halves are pinned by tests so the
decision is visible and cheaply reversible.

I would not have got this right by reasoning about it. The first version of that
test asserted the snippet keeps its accents, and it was the failure that
produced the real behaviour.

## 4. Closing the two-copies gap

`models.FTS_EXPRESSION` and the migration are necessarily two copies of one
expression — a migration must be frozen at the schema it shipped, so it cannot
import the constant. Nothing checked they agreed.

`test_fts_expression_matches_the_generated_columns` now reads the stored
definitions back out of the database with `pg_get_expr` and compares. That is
code against the *running schema*, not code against code, so it cannot pass by
both copies drifting together.

Two smaller guards went in beside it: that `unaccent` and the wrapper exist
after migration, and that the wrapper is declared `IMMUTABLE` — the property the
whole design rests on, since Postgres refuses a `STABLE` function in a generated
column and `unaccent()`'s one-argument form is stable.

## 5. Verification

Ten new tests. The four-way parametrize covers every stored/queried combination
of accented and ASCII, in both directions — a one-direction test would pass with
the mismatch merely relocated. `test_upgrade_downgrade_upgrade_cycle` already
runs `upgrade head → downgrade base → upgrade head`, so `0039`'s downgrade is
genuinely exercised and the re-upgrade proves the `IF NOT EXISTS` /
`CREATE OR REPLACE` are idempotent.

Full backend suite: 2100 passed, 7 skipped. `ruff`, `mypy` and `check_docs`
clean.
