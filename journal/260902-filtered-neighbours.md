# Previous/next follows the filter you were looking at

**Date:** 2026-09-02
**Branch:** `feat/140-filtered-neighbours`
**Issue:** [#140](https://github.com/johnmathews/library/issues/140)

## 1. What went

Filter the list to three documents, open one, and Previous/Next stepped to the
adjacent document **id in the whole archive** — almost never one of the three
you were just looking at. They now walk the filtered set.

## 2. The decision this does not reverse

`useDocumentNeighbors` carried a deliberate, documented choice: navigate by id,
**not** by the list's current sort, because sort-following was confusing — in
the default newest-first view, "Next" sent you to an *older*, lower id.

That decision stands, and it is worth being precise about why this does not
touch it. The complaint that killed sort-following was about **direction**.
This changes **membership**. Order within the set is still id-ascending, so
"Next" still means a higher id; there are simply fewer documents in the
sequence. Two different properties, and only one of them was ever rejected.

I wrote that reasoning into the composable's docblock as well as the doc,
because the next person to read "navigation is by id, not by the list's sort"
beside code that reads the list's filters deserves to find the distinction
already drawn.

## 3. What made it safe to keep the early exit

The scan stops as soon as it crosses below the current id: in a descending
scan, every later page holds only smaller ids. The issue flagged this as
needing a re-check, since the optimisation assumes a descending id-ish order.

It survives, and the reason is one line: **a filter is a `WHERE`, not an
`ORDER BY`.** Filtering removes rows; it does not reorder the ones that remain.
So a filtered scan is still `added_date desc`, still effectively id-descending,
and the same argument holds unchanged.

That is asserted rather than assumed — one test drives a 500-document filtered
set and asserts `listDocuments` was called exactly **once**. If the exit ever
regressed, every detail view would quietly start paging the whole set.

## 4. Two ways to make this silent, both avoided

**The set could differ from the list's.** The API-filter mapping lived inside
`DocumentListView` as a local `buildFilters`. Copying it into the neighbour
scan would have created two translations of the same filters, free to drift —
and a neighbour scan following a *slightly* different set from the list is
close to invisible from the UI. So the mapping moved into
`utils/documentQuery.ts` as `toDocumentFilters` and both callers use it. One
definition, two callers, rather than a test comparing two copies.

**The mode could be invisible.** Filtered and unfiltered navigation look
identical, so a scope indicator says which is in force. Three states, and the
third is the one that matters:

| state | shown |
| --- | --- |
| single-page filter set | `2 of 3` |
| set spans more than one page | `In your filtered results` |
| open document is not in the set | `No longer matches this filter` |

The last is not an error case. Relabelling a document from its own detail page
is precisely the workflow filtered navigation exists for, so dropping out of
your own filter set is *expected*. Navigation keeps walking the set — it must
not strand you — but claiming "2 of 3" would be a lie, so it says what actually
happened instead.

The middle row is a cost decision: counting a multi-page set means round-trips
the neighbour scan does not otherwise need. The position is withheld rather
than guessed at.

## 5. Verification

Fifteen new tests. These were written **after** the implementation, so they did
not go red first, and I validated them by mutation instead: forcing
`filtered = false` — exactly the pre-#140 behaviour — turned the four
filter-dependent cases red while the unfiltered ones correctly stayed green.
That is the property I actually wanted from a red-first run, obtained the other
way round.

One existing test changed. `'links a result to its detail page'` asserted the
href was exactly `/documents/12` while a search was active; the `?q=` was
incidental setup for the snippet block around it, so the assertion was
over-specified rather than protecting anything. It now checks the path, and the
query is asserted by two new tests that are actually about it.

Frontend suite: 112 files, 1442 tests. `npm run lint` and `npm run type-check`
clean.
