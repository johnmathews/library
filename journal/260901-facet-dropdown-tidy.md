# Sorting the facet dropdowns, and hiding a facet that cannot filter

**Date:** 2026-09-01
**Branch:** `facet-dropdown-tidy`

## 1. What went

Two small things the owner asked for on the document list's filter row:

1. **`Category` was unsorted.** Nineteen values in an order nobody could
   predict, because the order was the vocabulary's stored `ordinal` — which is
   seed-insertion order.
2. **`Property` offered exactly one value.** A select whose only real option is
   the same for every document it can return.

Both pickers — `FacetFilterBar.vue` on the list and `FacetEditor.vue` on a
document — now sort a facet's values by label. The filter bar additionally
renders a facet only once it has **two or more** values. Documented in
[docs/facets.md](../docs/facets.md) §2.1, which is new.

## 2. The ordinal was already incoherent, which settled where to sort

The first question was whether to sort in `load_vocabulary` (one change, every
consumer) or in the two components (display-only). Two facts decided it.

The first: `load_vocabulary`'s order is not merely presentational. It fixes the
order the vocabulary is listed in inside the **LLM labelling prompt**, and it is
what the `/vocabulary` admin panel exists to display and manage. Reordering it
would be a change to labelling behaviour dressed up as a UI fix, and the owner
asked for a UI fix.

The second removed the only argument for keeping the stored order in the
dropdown. Reading the live vocabulary showed the `category` ordinals are already
broken as a sequence: two values share `8`, and no value holds `14`. Whatever
curation the column was meant to carry, it is not carrying it. There was nothing
to preserve.

So the sort lives in the components, `load_vocabulary` is untouched, and both
component doc comments say why in case someone later reaches for the "obvious"
one-line server fix.

## 3. Why the threshold is two, and why nothing was deleted

`FacetFilterBar` already omitted facets with **zero** values, on the reasoning
that an empty select is worse than an absent one. The same reasoning extends one
step: a one-option select is a control that cannot partition anything the owner
wanted partitioned, because every document it can show carries that one value.

The obvious alternative was to delete the offending value so the existing
zero-value rule would hide the facet for free. That is a trap, and checking
before acting is what surfaced it: the value in question carries a **three-digit
number of document labels** on the live instance. Deleting it would destroy real
labelling data, and `DELETE /api/facets/{facet}/values/{value}` refuses with a
409 naming exactly that count — the API already knew this was a bad idea. Hiding
is a display decision; the labels stay and `?facet=` still filters on them.

The rule is keyed on the value **count**, never on a facet key. A hard-coded
exclusion would have been simpler and would have been silently wrong the day a
second value appears. As written, the facet returns to the bar on its own.

The editor's opposite rule is deliberate and unchanged: it renders every facet,
empty ones disabled with a "No values yet" hint, because the owner has to see a
facet exists before they can ask for a value in it. Only the option order inside
a select changed there.

## 4. The e2e journey depended on the old rule

`frontend/e2e/facets.spec.ts` creates a throwaway facet with **one** value,
applies it to a seeded document, then filters the list by it through
`facet-select-<key>`. Under the new threshold that select does not exist, and
the spec would have failed on all three browser projects — in the one gate that
cannot be run locally, on a change whose unit tests were entirely green.

The fix is one line of intent: the spec now creates two values and still applies
and filters on only the first. The second exists purely to clear the threshold,
which the comment at the creation site says, so nobody later "tidies" it away.

This is the third time in this repo that a rule change was fine everywhere it
was tested and broken in the place it wasn't. It was caught by reading the e2e
specs for dependencies on the changed behaviour *before* running anything —
which is the cheap step, and the one that keeps getting skipped.

## 5. Verification

- New guards run **red first**: the alphabetical assertions failed against the
  unmodified components (`['Any', 'Software', 'Energy']` vs the expected
  `['Any', 'Energy', 'Software']`; `['—', 'Software', 'Hardware']` vs
  `['—', 'Hardware', 'Software']`), and so did the one-value-hidden assertion.
- `npm run test:unit` — 112 files, 1416 tests, green.
- `npm run lint` (ESLint) and `npm run type-check` (vue-tsc) — clean.
- `scripts/check_docs.py docs/facets.md` — clean after re-stamping.
- The e2e change is exercised by CI's `e2e` job; it cannot be run here.

## 6. Also noted: the live instance is behind

Unrelated to this branch, but found while answering "is the latest version
deployed?" — it is not. `/healthz` reports a `git_sha` seven commits behind
`main`. Only one of the seven is user-facing (the chart rule editor); the rest
are CI and dependency bumps. Deploying is a separate, gated step and was not
done as part of this work.
